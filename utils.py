import fitz  # PyMuPDF
import requests
from bs4 import BeautifulSoup
import logging
import re

logger = logging.getLogger(__name__)

def extract_text_for_rag(file_path: str, max_chars: int = 20000) -> str:
    """Synchronous version for thread usage."""
    try:
        text = ""
        # Explicitly open and close
        doc = fitz.open(file_path)
        for page in doc:
            text += page.get_text()
            if len(text) > max_chars:
                text = text[:max_chars] + "..."
                break
        doc.close()
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        return ""

async def extract_text_from_pdf(file_path: str, max_chars: int = 20000) -> str:
    """Extracts text from a PDF file using PyMuPDF."""
    try:
        text = ""
        with fitz.open(file_path) as doc:
            for page in doc:
                text += page.get_text()
                if len(text) > max_chars:
                    text = text[:max_chars] + "..."
                    break
        return text.strip()
    except Exception as e:
        logger.error(f"Error extracting PDF text: {e}")
        return ""

async def scrape_web_page(url: str, max_chars: int = 15000) -> str:
    """Scrapes a web page and extracts meaningful text."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Use BeautifulSoup to parse HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()

        # Get text from paragraphs, headers, and list items
        elements = soup.find_all(['p', 'h1', 'h2', 'h3', 'li'])
        text_parts = [el.get_text().strip() for el in elements if len(el.get_text().strip()) > 20]
        
        full_text = "\n".join(text_parts)
        
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "..."
            
        return full_text.strip()
    except Exception as e:
        logger.error(f"Error scraping web page {url}: {e}")
        return ""

def find_urls(text: str) -> list:
    """Finds all URLs in a given text."""
    url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
    return re.findall(url_pattern, text)
