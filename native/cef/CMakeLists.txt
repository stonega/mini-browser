cmake_minimum_required(VERSION 3.21)
project(mini_browser_cef LANGUAGES C CXX)

if(NOT IS_DIRECTORY "${CEF_ROOT}/include")
  message(FATAL_ERROR "Set CEF_ROOT to an extracted official Linux CEF SDK")
endif()
list(APPEND CMAKE_MODULE_PATH "${CEF_ROOT}/cmake")
find_package(CEF REQUIRED)
add_subdirectory("${CEF_LIBCEF_DLL_WRAPPER_PATH}" libcef_dll_wrapper)

set(CEF_RUNTIME "${CMAKE_BINARY_DIR}/runtime")
file(MAKE_DIRECTORY "${CEF_RUNTIME}")
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
target_include_directories(mini_cef PRIVATE "${CEF_ROOT}")
target_link_libraries(mini_cef PRIVATE libcef_dll_wrapper "${CEF_ROOT}/Release/libcef.so")
set_target_properties(mini_cef PROPERTIES CXX_STANDARD 20 CXX_STANDARD_REQUIRED YES
  LIBRARY_OUTPUT_DIRECTORY "${CEF_RUNTIME}" BUILD_WITH_INSTALL_RPATH YES INSTALL_RPATH "$ORIGIN")

add_executable(cef-worker worker.cc)
target_include_directories(cef-worker PRIVATE "${CEF_ROOT}")
# libcef must precede libc in the helper's dependency order: the Linux sandbox
# resolves libc's close() with RTLD_NEXT. Do not load it through the Python shim.
target_link_libraries(cef-worker PRIVATE libcef_dll_wrapper "${CEF_ROOT}/Release/libcef.so")
set_target_properties(cef-worker PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CEF_RUNTIME}"
  BUILD_WITH_INSTALL_RPATH YES INSTALL_RPATH "$ORIGIN")
