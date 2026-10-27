cmake_minimum_required(VERSION 3.21)
project(mini_browser_cef LANGUAGES C CXX)

# GCC before 16 defaults to C++17, while current CEF headers require C++20.
# Apply this to the helper and upstream wrapper as well as the GTK bridge.
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# CEF's SDK recognizes "arm64", while Linux reports "aarch64". Without
# an explicit selection its fallback enables x86 compiler flags on ARM.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")
  set(PROJECT_ARCH "arm64")
elseif(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64)$")
  set(PROJECT_ARCH "x86_64")
else()
  message(FATAL_ERROR "Unsupported Chromium architecture: ${CMAKE_SYSTEM_PROCESSOR}")
endif()

if(NOT IS_DIRECTORY "${CEF_ROOT}/include")
  message(FATAL_ERROR "Set CEF_ROOT to an extracted official Linux CEF SDK")
endif()
list(APPEND CMAKE_MODULE_PATH "${CEF_ROOT}/cmake")
find_package(CEF REQUIRED)
add_subdirectory("${CEF_LIBCEF_DLL_WRAPPER_PATH}" libcef_dll_wrapper)

set(CEF_RUNTIME "${CMAKE_BINARY_DIR}/runtime")
file(MAKE_DIRECTORY "${CEF_RUNTIME}")
# Switching SDK providers must also remove resources/libraries that disappear
# from the new SDK, rather than silently loading an old provider's binary.
file(GLOB OLD_RUNTIME "${CEF_RUNTIME}/*")
foreach(OLD IN LISTS OLD_RUNTIME)
  get_filename_component(NAME "${OLD}" NAME)
  if(NOT NAME STREQUAL "libmini_cef.so" AND NOT NAME STREQUAL "cef-worker")
    file(REMOVE_RECURSE "${OLD}")
  endif()
endforeach()
file(GLOB SDK_RUNTIME "${CEF_ROOT}/Release/*" "${CEF_ROOT}/Resources/*")
list(APPEND SDK_RUNTIME "${CEF_ROOT}/LICENSE.txt" "${CEF_ROOT}/CREDITS.html")
foreach(SOURCE IN LISTS SDK_RUNTIME)
  get_filename_component(NAME "${SOURCE}" NAME)
  if(NAME STREQUAL "chrome-sandbox")
    continue()
  endif()
  file(REMOVE "${CEF_RUNTIME}/${NAME}")
  file(CREATE_LINK "${SOURCE}" "${CEF_RUNTIME}/${NAME}" SYMBOLIC)
endforeach()

add_library(mini_cef SHARED bridge.cc)
# Let libcef resolve its private codec dependencies alongside the bridge, without
# changing the GTK process's LD_LIBRARY_PATH or patching upstream binaries.
target_link_options(mini_cef PRIVATE "LINKER:--disable-new-dtags")
target_include_directories(mini_cef PRIVATE "${CEF_ROOT}")
target_link_libraries(mini_cef PRIVATE libcef_dll_wrapper "${CEF_ROOT}/Release/libcef.so")
set_target_properties(mini_cef PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED YES
  LIBRARY_OUTPUT_DIRECTORY "${CEF_RUNTIME}" BUILD_WITH_INSTALL_RPATH YES INSTALL_RPATH "$ORIGIN")

add_executable(cef-worker worker.cc)
target_link_options(cef-worker PRIVATE "LINKER:--disable-new-dtags")
target_include_directories(cef-worker PRIVATE "${CEF_ROOT}")
# libcef must precede libc in the helper's dependency order: the Linux sandbox
# resolves libc's close() with RTLD_NEXT. Do not load it through the Python shim.
target_link_libraries(cef-worker PRIVATE libcef_dll_wrapper "${CEF_ROOT}/Release/libcef.so")
set_target_properties(cef-worker PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CEF_RUNTIME}"
  BUILD_WITH_INSTALL_RPATH YES INSTALL_RPATH "$ORIGIN")
