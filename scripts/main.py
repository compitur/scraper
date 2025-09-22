import requests
from bs4 import BeautifulSoup

# URL to scrape
url = "https://obs.itu.edu.tr/public/DersProgram/DersProgramSearch?programSeviyeTipiAnahtari=LS&dersBransKoduId=3"

# Output file
output_file = "../data/scraped_table.txt"

def scrape_table():
    try:
        # Send a GET request to the URL
        response = requests.get(url)
        response.raise_for_status()

        # Parse the HTML content using BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the table
        table = soup.find('table')
        if not table:
            print("No table found on the page.")
            return

        # Extract table rows
        rows = table.find_all('tr')

        # Open the output file
        with open(output_file, 'w', encoding='utf-8') as file:
            for row in rows:
                # Extract columns from each row
                columns = row.find_all(['td', 'th'])
                # Extract specific columns (1st, 2nd, 3rd, 5th, 7th, 8th, 10th, 11th, 12th)
                selected_columns = [columns[i].get_text(strip=True) for i in [0, 1, 2, 4, 6, 7, 9, 10, 12] if i < len(columns)]
                # Write the selected columns to the file, separated by tabs
                file.write('\t'.join(selected_columns) + '\n')

        print(f"Table data has been written to {output_file}")

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while making the request: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    scrape_table()
