# Scraper

A C++ web scraper project using [cpr](https://github.com/libcpr/cpr) for HTTP requests and [gumbo-parser](https://github.com/google/gumbo-parser) for HTML parsing.

## Folder Structure

- `src/` - Source code
- `include/` - Header files
- `lib/` - External libraries (`cpr`, `gumbo-parser`)
- `data/` - Scraped data output
- `build/` - Build artifacts

## Build Instructions

1. Clone the dependencies:
    - `git clone https://github.com/libcpr/cpr.git lib/cpr`
    - `git clone https://github.com/google/gumbo-parser.git lib/gumbo-parser`
2. Create a build directory and run CMake:
    ```sh
    mkdir -p build
    cd build
    cmake ..
    make
    ```
3. Run the scraper:
    ```sh
    ./scraper
    ```

## Requirements
- CMake 3.14+
- A C++17 compatible compiler

## License
MIT
