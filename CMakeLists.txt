cmake_minimum_required(VERSION 3.14)
project(Scraper)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# --- CPR Library ---
# cpr has a CMakeLists.txt, so we can use add_subdirectory
add_subdirectory(lib/cpr)

# --- Gumbo Parser Library ---
# gumbo-parser does not have a CMakeLists.txt, so we manually create the library
# Find all the .c source files
file(GLOB GUMBO_SOURCES "lib/gumbo-parser/src/*.c")
# Create a static library from the source files
add_library(gumbo STATIC ${GUMBO_SOURCES})
# Make sure the include directory is available to targets that link against gumbo
target_include_directories(gumbo PUBLIC "lib/gumbo-parser/src")

# Main executable
add_executable(scraper src/main.cpp)

# Link libraries to the executable
target_link_libraries(scraper PRIVATE cpr::cpr gumbo)

# Add our own include directory
target_include_directories(scraper PRIVATE "${PROJECT_SOURCE_DIR}/include")
