#include <iostream>
#include <cpr/cpr.h>
#include "gumbo.h"
#include <fstream>

static std::string cleantext(GumboNode* node) {
    if (node->type == GUMBO_NODE_TEXT) {
        return std::string(node->v.text.text);
    } else if (node->type == GUMBO_NODE_ELEMENT &&
               node->v.element.tag != GUMBO_TAG_SCRIPT &&
               node->v.element.tag != GUMBO_TAG_STYLE) {
        std::string contents = "";
        GumboVector* children = &node->v.element.children;
        for (unsigned int i = 0; i < children->length; ++i) {
            contents += cleantext(static_cast<GumboNode*>(children->data[i]));
        }
        return contents;
    } else {
        return "";
    }
}

static void search_for_table_body(GumboNode* node, GumboNode** table_body) {
    if (*table_body || node->type != GUMBO_NODE_ELEMENT) {
        return;
    }

    if (node->v.element.tag == GUMBO_TAG_TBODY) {
        *table_body = node;
        return;
    }

    GumboVector* children = &node->v.element.children;
    for (unsigned int i = 0; i < children->length; ++i) {
        search_for_table_body(static_cast<GumboNode*>(children->data[i]), table_body);
    }
}

int main() {
    std::string url = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId=3";
    cpr::Response r = cpr::Get(cpr::Url{url});

    if (r.status_code != 200) {
        std::cerr << "URL'den veri çekilemedi. Durum Kodu: " << r.status_code << std::endl;
        return 1;
    }
    std::cout << "HTML başarıyla çekildi." << std::endl;

    // 2. HTML'i Gumbo ile ayrıştır
    GumboOutput* output = gumbo_parse(r.text.c_str());

    // 3. Ders tablosunun body'sini bul (tbody)
    GumboNode* table_body = nullptr;
    search_for_table_body(output->root, &table_body);

    // 4. Tablo bulunduysa, metnini temizle ve yazdır
    if (table_body) {
        std::cout << "\n--- Tablodan Çekilen Ham Metin ---\n" << std::endl;
        std::string table_text = cleantext(table_body);
        std::cout << table_text << std::endl;

        // 5. Metni bir dosyaya yaz
        std::ofstream outfile("../data/parsed_text.txt");
        if (outfile.is_open()) {
            outfile << table_text;
            outfile.close();
            std::cout << "\nMetin 'data/parsed_text.txt' dosyasına başarıyla yazıldı." << std::endl;
        } else {
            std::cerr << "\nDosya oluşturulamadı." << std::endl;
        }
    } else {
        std::cout << "Ders tablosu (tbody) bulunamadı." << std::endl;
    }

    // 6. Gumbo tarafından ayrılan belleği serbest bırak
    gumbo_destroy_output(&kGumboDefaultOptions, output);

    return 0;
}