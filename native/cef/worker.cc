// Renderer subprocess: Chromium applies its normal Linux sandbox here.
#include "include/cef_app.h"

int main(int argc, char **argv) {
  CefMainArgs arguments(argc, argv);
  const int result = CefExecuteProcess(arguments, nullptr, nullptr);
  return result < 0 ? 1 : result;
}
