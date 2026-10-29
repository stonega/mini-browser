// GTK/Chromium boundary. Browser data and script arguments are never logged.
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "include/cef_app.h"
#include "include/cef_browser.h"
#include "include/cef_client.h"
#include "include/cef_devtools_message_observer.h"
#include "include/cef_parser.h"
#include "include/cef_request_context.h"
#include "include/cef_task.h"
#include "include/cef_version.h"

namespace {
using PaintCallback = void (*)(int, const void *, int, int);
using EventCallback = void (*)(int, const char *, const char *);
using ResultCallback = void (*)(int, int, int, const void *, size_t);
PaintCallback paint_callback = nullptr;
EventCallback event_callback = nullptr;
ResultCallback result_callback = nullptr;
bool initialized = false;
bool context_ready = false;
bool allow_autoplay = false;
bool silent = false;
std::string disk_cache;

CefRefPtr<CefDictionaryValue> dict() { return CefDictionaryValue::Create(); }
void emit(int view, const char *type,
          CefRefPtr<CefDictionaryValue> data = nullptr) {
  if (!event_callback)
    return;
  auto value = CefValue::Create();
  value->SetDictionary(data ? data : dict());
  const auto json = CefWriteJSON(value, JSON_WRITER_DEFAULT).ToString();
  event_callback(view, type, json.c_str());
}
bool navigable(const CefString &url) {
  if (url == "about:blank")
    return true;
  CefURLParts parts;
  if (!CefParseURL(url, parts))
    return false;
  const auto scheme = CefString(&parts.scheme).ToString();
  return scheme == "http" || scheme == "https";
}

struct Reply {
  int view = 0;
  uint64_t prompt = 0;
  uint32_t permissions = 0;
  CefRefPtr<CefAuthCallback> auth;
  CefRefPtr<CefJSDialogCallback> javascript;
  CefRefPtr<CefFileDialogCallback> file;
  CefRefPtr<CefMediaAccessCallback> media;
  CefRefPtr<CefPermissionPromptCallback> permission;
};
struct PendingMedia {
  int request = 0;
  CefRefPtr<CefMediaAccessCallback> callback;
};
std::mutex reply_mutex;
std::atomic<int> next_reply{1};
std::map<int, Reply> replies;
int remember(Reply reply) {
  const int id = next_reply++;
  std::lock_guard<std::mutex> guard(reply_mutex);
  replies[id] = reply;
  return id;
}
void cancel_replies(int view) {
  std::vector<Reply> cancelled;
  {
    std::lock_guard<std::mutex> guard(reply_mutex);
    for (auto iterator = replies.begin(); iterator != replies.end();) {
      if (iterator->second.view == view) {
        cancelled.push_back(iterator->second);
        iterator = replies.erase(iterator);
      } else
        ++iterator;
    }
  }
  for (auto &reply : cancelled) {
    if (reply.auth)
      reply.auth->Cancel();
    if (reply.javascript)
      reply.javascript->Continue(false, "");
    if (reply.file)
      reply.file->Cancel();
    if (reply.media)
      reply.media->Cancel();
    // CEF dismisses a permission prompt itself when its browser closes.
  }
}
void dismiss_media_replies(int view) {
  std::vector<PendingMedia> cancelled;
  {
    std::lock_guard<std::mutex> guard(reply_mutex);
    for (auto iterator = replies.begin(); iterator != replies.end();) {
      if (iterator->second.view == view && iterator->second.media) {
        cancelled.push_back({iterator->first, iterator->second.media});
        iterator = replies.erase(iterator);
      } else
        ++iterator;
    }
  }
  for (auto &reply : cancelled) {
    reply.callback->Cancel();
    auto data = dict();
    data->SetInt("request", reply.request);
    emit(view, "permission-dismissed", data);
  }
}

class CloseTask final : public CefTask {
public:
  explicit CloseTask(CefRefPtr<CefBrowser> browser) : browser_(browser) {}
  void Execute() override { browser_->GetHost()->CloseBrowser(true); }

private:
  CefRefPtr<CefBrowser> browser_;
  IMPLEMENT_REFCOUNTING(CloseTask);
};
class Client;
std::map<int, CefRefPtr<Client>> views;
std::map<int, CefRefPtr<CefBeforeDownloadCallback>> destinations;
std::map<int, CefRefPtr<CefDownloadItemCallback>> downloads;

class Client final : public CefClient,
                     public CefRenderHandler,
                     public CefLifeSpanHandler,
                     public CefDisplayHandler,
                     public CefLoadHandler,
                     public CefRequestHandler,
                     public CefDevToolsMessageObserver,
                     public CefDownloadHandler,
                     public CefDialogHandler,
                     public CefJSDialogHandler,
                     public CefContextMenuHandler,
                     public CefPermissionHandler {
public:
  explicit Client(int identifier) : id(identifier) {}
  int id;
  int width = 420;
  int height = 734;
  int paint_width = 0;
  int paint_height = 0;
  int modifiers = 0;
  int buttons = 0;
  bool closing = false;
  bool creation_pending = false;
  bool popup_visible = false;
  CefRect popup_rect;
  int popup_width = 0;
  int popup_height = 0;
  std::vector<unsigned char> pixels;
  std::vector<unsigned char> popup_pixels;
  CefRefPtr<CefBrowser> browser;
  CefRefPtr<CefRegistration> observer;

  CefRefPtr<CefRenderHandler> GetRenderHandler() override { return this; }
  CefRefPtr<CefLifeSpanHandler> GetLifeSpanHandler() override { return this; }
  CefRefPtr<CefDisplayHandler> GetDisplayHandler() override { return this; }
  CefRefPtr<CefLoadHandler> GetLoadHandler() override { return this; }
  CefRefPtr<CefRequestHandler> GetRequestHandler() override { return this; }
  CefRefPtr<CefDownloadHandler> GetDownloadHandler() override { return this; }
  CefRefPtr<CefDialogHandler> GetDialogHandler() override { return this; }
  CefRefPtr<CefJSDialogHandler> GetJSDialogHandler() override { return this; }
  CefRefPtr<CefContextMenuHandler> GetContextMenuHandler() override {
    return this;
  }
  CefRefPtr<CefPermissionHandler> GetPermissionHandler() override {
    return this;
  }

  void Create() {
    creation_pending = true;
    CefWindowInfo window;
    window.SetAsWindowless(0);
    CefBrowserSettings settings;
    settings.windowless_frame_rate = 30;
    CefBrowserHost::CreateBrowser(window, this, "about:blank", settings,
                                  nullptr, nullptr);
  }
  void GetViewRect(CefRefPtr<CefBrowser>, CefRect &rect) override {
    rect = CefRect(0, 0, width, height);
  }
  void OnPaint(CefRefPtr<CefBrowser>, PaintElementType type, const RectList &,
               const void *buffer, int w, int h) override {
    if (!paint_callback || closing || w < 1 || h < 1 || w > 4096 || h > 4096)
      return;
    const auto *bytes = static_cast<const unsigned char *>(buffer);
    if (type == PET_VIEW) {
      paint_width = w;
      paint_height = h;
      pixels.assign(bytes, bytes + static_cast<size_t>(w) * h * 4);
    } else {
      popup_width = w;
      popup_height = h;
      popup_pixels.assign(bytes, bytes + static_cast<size_t>(w) * h * 4);
    }
    Present();
  }
  void Present() {
    if (pixels.size() != static_cast<size_t>(paint_width) * paint_height * 4)
      return;
    if (!popup_visible || popup_pixels.empty()) {
      paint_callback(id, pixels.data(), paint_width, paint_height);
      return;
    }
    auto composed = pixels;
    for (int row = 0; row < popup_height; ++row) {
      const int y = popup_rect.y + row;
      if (y < 0 || y >= paint_height)
        continue;
      const int left = std::max(0, popup_rect.x);
      const int right = std::min(paint_width, popup_rect.x + popup_width);
      if (right <= left)
        continue;
      std::memcpy(
          composed.data() + (static_cast<size_t>(y) * paint_width + left) * 4,
          popup_pixels.data() +
              (static_cast<size_t>(row) * popup_width + left - popup_rect.x) *
                  4,
          static_cast<size_t>(right - left) * 4);
    }
    paint_callback(id, composed.data(), paint_width, paint_height);
  }
  void OnPopupShow(CefRefPtr<CefBrowser>, bool show) override {
    popup_visible = show;
    if (!show)
      popup_pixels.clear();
    Present();
  }
  void OnPopupSize(CefRefPtr<CefBrowser>, const CefRect &rect) override {
    popup_rect = rect;
  }
  void OnImeCompositionRangeChanged(CefRefPtr<CefBrowser>, const CefRange &,
                                    const RectList &bounds) override {
    if (bounds.empty())
      return;
    auto data = dict();
    data->SetInt("x", bounds.back().x);
    data->SetInt("y", bounds.back().y);
    data->SetInt("width", bounds.back().width);
    data->SetInt("height", bounds.back().height);
    emit(id, "caret", data);
  }
  void OnAfterCreated(CefRefPtr<CefBrowser> created) override {
    browser = created;
    observer = browser->GetHost()->AddDevToolsMessageObserver(this);
    if (closing) {
      // Creation still accesses its platform delegate after this callback.
      // Defer close until CreateBrowser has unwound, including immediately
      // closed GTK windows whose asynchronous browser has only just appeared.
      CefPostTask(TID_UI, new CloseTask(created));
    } else
      emit(id, "ready");
  }
  void OnBeforeClose(CefRefPtr<CefBrowser>) override {
    cancel_replies(id);
    observer = nullptr;
    browser = nullptr;
    emit(id, "closed");
    views.erase(id);
  }
  void OnTitleChange(CefRefPtr<CefBrowser>, const CefString &title) override {
    auto data = dict();
    data->SetString("title", title);
    emit(id, "title", data);
  }
  void OnAddressChange(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                       const CefString &url) override {
    if (!frame->IsMain() || !navigable(url))
      return;
    auto data = dict();
    data->SetString("uri", url);
    emit(id, "uri", data);
  }
  bool OnConsoleMessage(CefRefPtr<CefBrowser>, cef_log_severity_t,
                        const CefString &, const CefString &, int) override {
    return true;
  }
  void OnLoadStart(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                   TransitionType) override {
    if (frame->IsMain())
      emit(id, "load-start");
  }
  void OnLoadingStateChange(CefRefPtr<CefBrowser>, bool loading, bool back,
                            bool forward) override {
    auto data = dict();
    data->SetBool("loading", loading);
    data->SetBool("back", back);
    data->SetBool("forward", forward);
    emit(id, "loading", data);
  }
  void OnLoadError(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                   ErrorCode code, const CefString &,
                   const CefString &url) override {
    if (!frame->IsMain() || code == ERR_ABORTED)
      return;
    auto data = dict();
    data->SetInt("code", code);
    data->SetString("uri", url);
    emit(id, code <= -200 && code >= -299 ? "tls-error" : "load-error", data);
  }
  bool OnBeforeBrowse(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame> frame,
                      CefRefPtr<CefRequest> request, bool, bool) override {
    if (frame->IsMain()) {
      dismiss_media_replies(id);
      if (!navigable(request->GetURL())) {
        emit(id, "unsupported-uri");
        return true;
      }
    }
    return false;
  }
  bool OnBeforePopup(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>, int,
                     const CefString &url, const CefString &,
                     cef_window_open_disposition_t, bool,
                     const CefPopupFeatures &, CefWindowInfo &,
                     CefRefPtr<CefClient> &, CefBrowserSettings &,
                     CefRefPtr<CefDictionaryValue> &, bool *) override {
    if (browser && url != "about:blank" && navigable(url))
      browser->GetMainFrame()->LoadURL(url);
    return true;
  }
  bool OnOpenURLFromTab(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                        const CefString &url, cef_window_open_disposition_t,
                        bool) override {
    if (browser && navigable(url))
      browser->GetMainFrame()->LoadURL(url);
    return true;
  }
  void OnRenderProcessTerminated(CefRefPtr<CefBrowser>, TerminationStatus, int,
                                 const CefString &) override {
    emit(id, "terminated");
  }
  void OnDevToolsMethodResult(CefRefPtr<CefBrowser>, int message, bool success,
                              const void *result, size_t size) override {
    if (!result_callback)
      return;
    if (size > 8 * 1024 * 1024)
      result_callback(id, message, 0, "{}", 2);
    else
      result_callback(id, message, success, result, size);
  }
  void OnDevToolsEvent(CefRefPtr<CefBrowser>, const CefString &method,
                       const void *parameters, size_t size) override {
    const auto name = method.ToString();
    if (name.rfind("Runtime.executionContext", 0) != 0 &&
        name != "Runtime.bindingCalled" &&
        name != "Page.frameNavigated" &&
        name != "Page.navigatedWithinDocument" &&
        name.rfind("WebAudio.context", 0) != 0 &&
        name != "Media.playerEventsAdded" &&
        name != "Media.playerPropertiesChanged")
      return;
    if (!event_callback || size > 8 * 1024 * 1024)
      return;
    const std::string json(static_cast<const char *>(parameters), size);
    event_callback(id, name.c_str(), json.empty() ? "{}" : json.c_str());
  }
  bool GetAuthCredentials(CefRefPtr<CefBrowser>, const CefString &origin,
                          bool proxy, const CefString &host, int port,
                          const CefString &realm, const CefString &,
                          CefRefPtr<CefAuthCallback> callback) override {
    Reply reply;
    reply.view = id;
    reply.auth = callback;
    auto data = dict();
    data->SetInt("request", remember(reply));
    data->SetString("origin", origin);
    data->SetString("host", host);
    data->SetString("realm", realm);
    data->SetInt("port", port);
    data->SetBool("proxy", proxy);
    emit(id, "authenticate", data);
    return true;
  }
  bool OnJSDialog(CefRefPtr<CefBrowser>, const CefString &origin,
                  JSDialogType type, const CefString &message,
                  const CefString &prompt,
                  CefRefPtr<CefJSDialogCallback> callback, bool &) override {
    Reply reply;
    reply.view = id;
    reply.javascript = callback;
    auto data = dict();
    data->SetInt("request", remember(reply));
    data->SetInt("type", type);
    data->SetString("origin", origin);
    data->SetString("message", message);
    data->SetString("prompt", prompt);
    emit(id, "javascript-dialog", data);
    return true;
  }
  bool OnFileDialog(CefRefPtr<CefBrowser>, FileDialogMode mode,
                    const CefString &title, const CefString &,
                    const std::vector<CefString> &filters,
                    const std::vector<CefString> &,
                    const std::vector<CefString> &,
                    CefRefPtr<CefFileDialogCallback> callback) override {
    Reply reply;
    reply.view = id;
    reply.file = callback;
    auto data = dict();
    data->SetInt("request", remember(reply));
    data->SetInt("mode", mode);
    data->SetString("title", title);
    auto values = CefListValue::Create();
    for (size_t i = 0; i < filters.size(); ++i)
      values->SetString(i, filters[i]);
    data->SetList("filters", values);
    emit(id, "file-dialog", data);
    return true;
  }
  bool OnRequestMediaAccessPermission(
      CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
      const CefString &requesting_origin, uint32_t requested_permissions,
      CefRefPtr<CefMediaAccessCallback> callback) override {
    Reply reply;
    reply.view = id;
    reply.permissions = requested_permissions;
    reply.media = callback;
    auto data = dict();
    data->SetInt("request", remember(reply));
    data->SetString("origin", requesting_origin);
    data->SetInt("permissions", static_cast<int>(requested_permissions));
    emit(id, "media-permission", data);
    return true;
  }
  bool OnShowPermissionPrompt(
      CefRefPtr<CefBrowser>, uint64_t prompt_id,
      const CefString &requesting_origin, uint32_t requested_permissions,
      CefRefPtr<CefPermissionPromptCallback> callback) override {
    Reply reply;
    reply.view = id;
    reply.prompt = prompt_id;
    reply.permissions = requested_permissions;
    reply.permission = callback;
    auto data = dict();
    data->SetInt("request", remember(reply));
    data->SetString("origin", requesting_origin);
    data->SetInt("permissions", static_cast<int>(requested_permissions));
    emit(id, "permission", data);
    return true;
  }
  void OnDismissPermissionPrompt(
      CefRefPtr<CefBrowser>, uint64_t prompt_id,
      cef_permission_request_result_t) override {
    int request = 0;
    {
      std::lock_guard<std::mutex> guard(reply_mutex);
      for (auto iterator = replies.begin(); iterator != replies.end(); ++iterator) {
        if (iterator->second.permission && iterator->second.prompt == prompt_id) {
          request = iterator->first;
          replies.erase(iterator);
          break;
        }
      }
    }
    if (request) {
      auto data = dict();
      data->SetInt("request", request);
      emit(id, "permission-dismissed", data);
    }
  }
  void OnBeforeContextMenu(CefRefPtr<CefBrowser>, CefRefPtr<CefFrame>,
                           CefRefPtr<CefContextMenuParams> parameters,
                           CefRefPtr<CefMenuModel> model) override {
    model->Clear();
    auto data = dict();
    data->SetInt("x", parameters->GetXCoord());
    data->SetInt("y", parameters->GetYCoord());
    emit(id, "context-menu", data);
  }
  bool
  OnBeforeDownload(CefRefPtr<CefBrowser>, CefRefPtr<CefDownloadItem> item,
                   const CefString &suggested,
                   CefRefPtr<CefBeforeDownloadCallback> callback) override {
    const int download = item->GetId();
    destinations[download] = callback;
    auto data = dict();
    data->SetInt("id", download);
    data->SetString("name", suggested);
    data->SetString("uri", item->GetURL());
    emit(id, "download", data);
    return true;
  }
  void OnDownloadUpdated(CefRefPtr<CefBrowser>, CefRefPtr<CefDownloadItem> item,
                         CefRefPtr<CefDownloadItemCallback> callback) override {
    const int download = item->GetId();
    downloads[download] = callback;
    auto data = dict();
    data->SetInt("id", download);
    data->SetBool("complete", item->IsComplete());
    data->SetBool("cancelled", item->IsCanceled());
    data->SetBool("progress", item->IsInProgress());
    data->SetDouble("received", item->GetReceivedBytes());
    data->SetDouble("total", item->GetTotalBytes());
    emit(id, "download-update", data);
    if (!item->IsInProgress()) {
      downloads.erase(download);
      destinations.erase(download);
    }
  }

private:
  IMPLEMENT_REFCOUNTING(Client);
};

class App final : public CefApp, public CefBrowserProcessHandler {
public:
  CefRefPtr<CefBrowserProcessHandler> GetBrowserProcessHandler() override {
    return this;
  }
  void
  OnBeforeCommandLineProcessing(const CefString &process_type,
                                CefRefPtr<CefCommandLine> command) override {
    if (!process_type.empty())
      return;
    command->AppendSwitchWithValue("ozone-platform", "headless");
    command->AppendSwitchWithValue("password-store", "basic");
    command->AppendSwitchWithValue("autoplay-policy",
                                   allow_autoplay ? "no-user-gesture-required"
                                                  : "user-gesture-required");
    command->AppendSwitchWithValue("disk-cache-dir", disk_cache);
    command->AppendSwitch("disable-extensions");
    command->AppendSwitch("disable-setuid-sandbox");
    if (silent)
      command->AppendSwitch("mute-audio");
  }
  void OnContextInitialized() override {
    auto context = CefRequestContext::GetGlobalContext();
    for (const char *name :
         {"credentials_enable_service", "credentials_enable_autosignin",
          "autofill.profile_enabled", "autofill.credit_card_enabled"}) {
      if (!context->HasPreference(name))
        continue;
      auto value = CefValue::Create();
      value->SetBool(false);
      CefString error;
      context->SetPreference(name, value, error);
    }
    auto proxy = dict();
    proxy->SetString("mode", "system");
    auto value = CefValue::Create();
    value->SetDictionary(proxy);
    CefString error;
    context->SetPreference("proxy", value, error);
    context_ready = true;
    for (auto &[id, client] : views)
      if (!client->closing)
        client->Create();
  }

private:
  IMPLEMENT_REFCOUNTING(App);
};
Client *view(int id) {
  auto iterator = views.find(id);
  return iterator == views.end() ? nullptr : iterator->second.get();
}
CefRefPtr<CefBrowser> page(int id) {
  auto *client = view(id);
  return client ? client->browser : nullptr;
}
} // namespace

extern "C" {
int mini_cef_abi_version() { return 4; }
const char *mini_cef_version() { return CEF_VERSION; }
int mini_cef_initialize(const char *runtime, const char *profile,
                        const char *cache, int autoplay, int mute,
                        PaintCallback paint, EventCallback event,
                        ResultCallback result) {
  if (initialized)
    return 0;
  disk_cache = cache;
  allow_autoplay = autoplay != 0;
  silent = mute != 0;
  paint_callback = paint;
  event_callback = event;
  result_callback = result;
  int argc = 1;
  char executable[] = "mini-browser";
  char *argv[] = {executable, nullptr};
  CefMainArgs arguments(argc, argv);
  CefSettings settings;
  settings.no_sandbox = false;
  settings.windowless_rendering_enabled = true;
  settings.log_severity = LOGSEVERITY_DISABLE;
  CefString(&settings.browser_subprocess_path) =
      std::string(runtime) + "/cef-worker";
  CefString(&settings.resources_dir_path) = runtime;
  CefString(&settings.locales_dir_path) = std::string(runtime) + "/locales";
  CefString(&settings.root_cache_path) = profile;
  CefString(&settings.cache_path) = std::string(profile) + "/default";
  initialized = CefInitialize(arguments, settings, new App, nullptr);
  return initialized;
}
void mini_cef_pump() {
  if (initialized)
    CefDoMessageLoopWork();
}
void mini_cef_create(int id) {
  if (views.count(id))
    return;
  views[id] = new Client(id);
  if (context_ready)
    views[id]->Create();
}
void mini_cef_close(int id) {
  auto *client = view(id);
  if (!client)
    return;
  client->closing = true;
  cancel_replies(id);
  if (!client->browser && !client->creation_pending) {
    views.erase(id);
    return;
  }
  if (client->browser) {
    client->browser->GetHost()->SetAudioMuted(true);
    client->browser->GetHost()->CloseBrowser(true);
  }
}
void mini_cef_resize(int id, int width, int height) {
  auto *client = view(id);
  if (!client)
    return;
  client->width = std::clamp(width, 1, 4096);
  client->height = std::clamp(height, 1, 4096);
  if (client->browser)
    client->browser->GetHost()->WasResized();
}
void mini_cef_load(int id, const char *url) {
  auto browser = page(id);
  if (browser && navigable(url))
    browser->GetMainFrame()->LoadURL(url);
}
void mini_cef_stop(int id) {
  auto browser = page(id);
  if (browser)
    browser->StopLoad();
}
void mini_cef_reload(int id, int fresh) {
  auto browser = page(id);
  if (browser) {
    if (fresh)
      browser->ReloadIgnoreCache();
    else
      browser->Reload();
  }
}
void mini_cef_focus(int id, int focused) {
  auto browser = page(id);
  if (browser)
    browser->GetHost()->SetFocus(focused != 0);
}
void mini_cef_mute(int id, int muted) {
  auto browser = page(id);
  if (browser)
    browser->GetHost()->SetAudioMuted(muted != 0);
}
void mini_cef_zoom(int id, double factor) {
  auto browser = page(id);
  if (browser)
    browser->GetHost()->SetZoomLevel(factor);
}
void mini_cef_frame_rate(int id, int rate) {
  auto browser = page(id);
  if (browser)
    browser->GetHost()->SetWindowlessFrameRate(rate);
}
void mini_cef_mouse(int id, int x, int y, int button, int released,
                    int clicks) {
  auto browser = page(id);
  if (!browser)
    return;
  auto *client = view(id);
  const int flag = button == 1   ? EVENTFLAG_LEFT_MOUSE_BUTTON
                   : button == 2 ? EVENTFLAG_MIDDLE_MOUSE_BUTTON
                   : button == 3 ? EVENTFLAG_RIGHT_MOUSE_BUTTON
                                 : 0;
  if (button) {
    if (released)
      client->buttons &= ~flag;
    else
      client->buttons |= flag;
  }
  CefMouseEvent event;
  event.x = x;
  event.y = y;
  event.modifiers = client->modifiers | client->buttons;
  if (!button)
    browser->GetHost()->SendMouseMoveEvent(event, false);
  else
    browser->GetHost()->SendMouseClickEvent(event,
                                            button == 3   ? MBT_RIGHT
                                            : button == 2 ? MBT_MIDDLE
                                                          : MBT_LEFT,
                                            released != 0, clicks);
}
void mini_cef_wheel(int id, int x, int y, int dx, int dy) {
  auto browser = page(id);
  if (!browser)
    return;
  CefMouseEvent event;
  event.x = x;
  event.y = y;
  browser->GetHost()->SendMouseWheelEvent(event, dx, dy);
}
void mini_cef_key(int id, int code, int native_code, int modifiers,
                  int released) {
  auto browser = page(id);
  if (!browser)
    return;
  view(id)->modifiers = modifiers;
  CefKeyEvent event;
  event.type = released ? KEYEVENT_KEYUP : KEYEVENT_RAWKEYDOWN;
  event.windows_key_code = code;
  event.native_key_code = native_code;
  event.modifiers = modifiers;
  browser->GetHost()->SendKeyEvent(event);
}
void mini_cef_text(int id, const char *text) {
  auto browser = page(id);
  if (browser)
    browser->GetHost()->ImeCommitText(text, CefRange::InvalidRange(), 0);
}
void mini_cef_preedit(int id, const char *text, int cursor) {
  auto browser = page(id);
  if (!browser)
    return;
  const CefString value(text);
  CefCompositionUnderline underline;
  underline.range = CefRange(0, value.length());
  underline.color = 0xff000000;
  browser->GetHost()->ImeSetComposition(
      value, {underline}, CefRange::InvalidRange(), CefRange(cursor, cursor));
}
int mini_cef_command(int id, int message, const char *method,
                     const char *json) {
  auto browser = page(id);
  if (!browser)
    return 0;
  auto value = CefParseJSON(json, JSON_PARSER_RFC);
  if (!value || value->GetType() != VTYPE_DICTIONARY)
    return 0;
  return browser->GetHost()->ExecuteDevToolsMethod(message, method,
                                                   value->GetDictionary());
}
void mini_cef_download(int id, const char *path) {
  auto iterator = destinations.find(id);
  if (iterator == destinations.end())
    return;
  auto callback = iterator->second;
  destinations.erase(iterator);
  if (path && *path)
    callback->Continue(path, false);
  else {
    auto cancel = downloads.find(id);
    if (cancel != downloads.end())
      cancel->second->Cancel();
  }
}
void mini_cef_cancel_download(int id) {
  destinations.erase(id);
  auto iterator = downloads.find(id);
  if (iterator != downloads.end())
    iterator->second->Cancel();
}
void mini_cef_reply(int id, int accepted, const char *json) {
  Reply reply;
  {
    std::lock_guard<std::mutex> guard(reply_mutex);
    auto iterator = replies.find(id);
    if (iterator == replies.end())
      return;
    reply = iterator->second;
    replies.erase(iterator);
  }
  auto value = CefParseJSON(json, JSON_PARSER_RFC);
  auto data = value && value->GetType() == VTYPE_DICTIONARY
                  ? value->GetDictionary()
                  : dict();
  if (reply.auth) {
    if (accepted)
      reply.auth->Continue(data->GetString("username"),
                           data->GetString("password"));
    else
      reply.auth->Cancel();
  }
  if (reply.javascript)
    reply.javascript->Continue(accepted != 0, data->GetString("text"));
  if (reply.file) {
    if (!accepted)
      reply.file->Cancel();
    else {
      std::vector<CefString> paths;
      auto list = data->GetList("paths");
      if (list)
        for (size_t i = 0; i < list->GetSize(); ++i)
          paths.push_back(list->GetString(i));
      reply.file->Continue(paths);
    }
  }
  if (reply.media) {
    if (accepted)
      reply.media->Continue(reply.permissions);
    else
      reply.media->Cancel();
  }
  if (reply.permission)
    reply.permission->Continue(accepted ? CEF_PERMISSION_RESULT_ACCEPT
                                        : CEF_PERMISSION_RESULT_DENY);
}
int mini_cef_shutdown() {
  if (!initialized)
    return 1;
  std::vector<int> identifiers;
  for (auto &[id, client] : views)
    identifiers.push_back(id);
  for (int id : identifiers)
    mini_cef_close(id);
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::seconds(5);
  while (!views.empty() && std::chrono::steady_clock::now() < deadline) {
    CefDoMessageLoopWork();
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  if (!views.empty())
    return 0;
  downloads.clear();
  destinations.clear();
  CefShutdown();
  initialized = false;
  context_ready = false;
  paint_callback = nullptr;
  event_callback = nullptr;
  result_callback = nullptr;
  return 1;
}
}
