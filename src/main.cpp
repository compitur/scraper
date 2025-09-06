#include <iostream>
#include <cpr/cpr.h>
#include "gumbo.h"

int main() {
    std::cout << "Running scraper..." << std::endl;

    cpr::Response r = cpr::Get(cpr::Url{"http://www.example.com"});
    
    if (r.status_code == 200) {
        std::cout << "Successfully fetched example.com with status code: " << r.status_code << std::endl;
        
        // Parse the HTML content with Gumbo
        GumboOutput* output = gumbo_parse(r.text.c_str());
        
        std::cout << "Gumbo has parsed the HTML." << std::endl;
        
        // Don't forget to free the memory allocated by gumbo
        gumbo_destroy_output(&kGumboDefaultOptions, output);
        
        std::cout << "Scraper setup successful!" << std::endl;
    } else {
        std::cerr << "Failed to fetch URL, status code: " << r.status_code << std::endl;
    }

    return 0;
}
