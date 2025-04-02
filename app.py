import streamlit as st
import os
import tempfile
import pandas as pd
from PIL import Image
import google.generativeai as genai
import fitz  # PyMuPDF
import io
import json
import re
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure API key
api_key = os.getenv("google_api") or "AIzaSyDLf-uRK3WaT3wyF_LGTYr3Ll_Gu1bwzYg"
genai.configure(api_key=api_key)

# Initialize Gemini model - using gemini-1.5-flash
model = genai.GenerativeModel('gemini-1.5-flash')

def extract_images_from_pdf(pdf_file):
    """Extract images from PDF file"""
    # Open the PDF file
    pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
    images = []
    
    # Iterate through each page
    for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]
        image_list = page.get_images(full=True)
        
        # Extract each image
        for img_index, img in enumerate(image_list):
            xref = img[0]
            base_image = pdf_document.extract_image(xref)
            image_bytes = base_image["image"]
            
            # Convert to PIL Image
            image = Image.open(io.BytesIO(image_bytes))
            images.append(image)
    
    pdf_document.close()
    return images

def extract_invoice_data(image):
    """Extract invoice data from an image using Gemini"""
    prompt = """
    Extract the following information from this invoice image:
    1. Invoice Number
    2. Invoice Date
    3. Vendor/Company Name
    4. Total Amount
    5. Items with their quantities and prices (if visible)
    
    Format the output as a JSON with these fields.
    """
    
    try:
        # Convert PIL Image to bytes for Gemini API
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format=image.format or 'PNG')
        img_byte_arr = img_byte_arr.getvalue()
        
        response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": img_byte_arr}])
        return response.text
    except Exception as e:
        return f"Error: {str(e)}"

def parse_json_from_text(text):
    """Extract JSON data from text that might contain markdown code blocks"""
    # Try to find JSON in code blocks
    json_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', text)
    
    if json_match:
        json_str = json_match.group(1)
    else:
        # If no code blocks, try to find JSON directly
        json_match = re.search(r'({[\s\S]*})', text)
        if json_match:
            json_str = json_match.group(1)
        else:
            return None
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None

def flatten_items(items):
    """Flatten items list to string for Excel"""
    if not items:
        return ""
    
    flat_items = []
    for idx, item in enumerate(items):
        item_str = f"Item {idx+1}: "
        item_details = []
        for k, v in item.items():
            item_details.append(f"{k}: {v}")
        item_str += ", ".join(item_details)
        flat_items.append(item_str)
    
    return "; ".join(flat_items)

def main():
    st.title("PDF Invoice Data Extractor")
    st.write("Upload multiple PDFs containing invoice images, and get an Excel sheet with the extracted data.")
    
    # File uploader
    uploaded_files = st.file_uploader("Upload PDF files", type="pdf", accept_multiple_files=True)
    
    if uploaded_files:
        if st.button("Extract Data"):
            all_invoice_data = []
            progress_bar = st.progress(0)
            
            for i, pdf_file in enumerate(uploaded_files):
                st.write(f"Processing: {pdf_file.name}")
                
                # Extract images from PDF
                images = extract_images_from_pdf(pdf_file)
                
                if not images:
                    st.warning(f"No images found in {pdf_file.name}")
                    continue
                
                # Display each image and extract data
                for j, image in enumerate(images):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.image(image, caption=f"Image {j+1} from {pdf_file.name}", width=300)
                    
                    with col2:
                        raw_data = extract_invoice_data(image)
                        st.text_area(f"Extracted Data {j+1}", raw_data, height=250)
                        
                        # Parse JSON from the extracted text
                        parsed_data = parse_json_from_text(raw_data)
                        
                        if parsed_data:
                            # Extract the main fields
                            invoice_entry = {
                                "PDF_File": pdf_file.name,
                                "Invoice_Number": parsed_data.get("Invoice Number", ""),
                                "Invoice_Date": parsed_data.get("Invoice Date", ""),
                                "Vendor_Name": parsed_data.get("Vendor/Company Name", ""),
                                "Total_Amount": parsed_data.get("Total Amount", "")
                            }
                            
                            # Handle items differently - flatten them for the Excel
                            items = parsed_data.get("Items", [])
                            invoice_entry["Items_Summary"] = flatten_items(items)
                            
                            all_invoice_data.append(invoice_entry)
                        else:
                            st.warning(f"Could not parse JSON data from extraction {j+1}")
                
                # Update progress
                progress_bar.progress((i + 1) / len(uploaded_files))
            
            # Convert to DataFrame and export to CSV (more compatible with Streamlit Share)
            if all_invoice_data:
                df = pd.DataFrame(all_invoice_data)
                
                # Show preview of the data
                st.subheader("Data Preview")
                st.dataframe(df)
                
                # Create CSV for download instead of Excel
                csv = df.to_csv(index=False)
                
                # Provide download link
                st.download_button(
                    label="Download CSV",
                    data=csv,
                    file_name="invoice_data.csv",
                    mime="text/csv"
                )
                
                # Optionally, still provide Excel if user wants it
                try:
                    # Create a temporary file for the Excel
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                        excel_path = tmp.name
                        df.to_excel(excel_path, index=False)
                    
                    # Provide download link for Excel
                    with open(excel_path, "rb") as file:
                        st.download_button(
                            label="Download Excel (may not work on Streamlit Share)",
                            data=file,
                            file_name="invoice_data.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="excel_download"
                        )
                    
                    # Clean up the temp file
                    os.unlink(excel_path)
                except ImportError:
                    st.info("Excel export not available. Install openpyxl for Excel support.")
            else:
                st.warning("No data was extracted from the PDFs.")

if __name__ == "__main__":
    main()
