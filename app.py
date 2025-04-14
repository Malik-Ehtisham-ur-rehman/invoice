import streamlit as st
import os
import tempfile
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
import google.generativeai as genai
import fitz  # PyMuPDF
import io
import json
import re
import datetime
import time
import numpy as np
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure API key
api_key = os.getenv("google_api") or "AIzaSyDLf-uRK3WaT3wyF_LGTYr3Ll_Gu1bwzYg"
genai.configure(api_key=api_key)

# Initialize Gemini model - using gemini-1.5-flash
model = genai.GenerativeModel('gemini-1.5-flash')

# Define limitations
MAX_INVOICE_IMAGES = 5  # Maximum images per session
MAX_INVOICES_PER_WEEK = 15  # Maximum invoices per week globally

# File path for storing usage data
USAGE_FILE_PATH = ".streamlit/usage_data.json"

def text_to_image(text, width=400, padding=10):
    """Convert text to image to prevent copying"""
    # Calculate needed height based on text length
    font_size = 12
    lines = text.count('\n') + 1
    height = max(lines * (font_size + 4) + padding * 2, 250)  # Minimum height of 250px
    
    # Create image with white background
    img = Image.new('RGB', (width, height), color='white')
    d = ImageDraw.Draw(img)
    
    # Use default font
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()
    
    # Draw text
    d.text((padding, padding), text, fill='black', font=font)
    
    return img

def ensure_directory_exists():
    """Make sure the .streamlit directory exists"""
    os.makedirs(os.path.dirname(USAGE_FILE_PATH), exist_ok=True)

def load_usage_data():
    """Load global usage data from file"""
    ensure_directory_exists()
    try:
        if os.path.exists(USAGE_FILE_PATH):
            with open(USAGE_FILE_PATH, 'r') as f:
                data = json.load(f)
                return data
        else:
            # Initialize with empty data
            return {"timestamps": []}
    except Exception as e:
        st.error(f"Error loading usage data: {e}")
        return {"timestamps": []}

def save_usage_data(data):
    """Save usage data to file"""
    ensure_directory_exists()
    try:
        with open(USAGE_FILE_PATH, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        st.error(f"Error saving usage data: {e}")

def get_weekly_usage_count():
    """Count how many invoices have been processed in the past week"""
    usage_data = load_usage_data()
    
    # Convert string timestamps back to datetime objects
    timestamps = []
    for ts_str in usage_data.get("timestamps", []):
        try:
            timestamps.append(datetime.datetime.fromisoformat(ts_str))
        except ValueError:
            # Skip invalid timestamps
            pass
    
    # Filter for last week
    now = datetime.datetime.now()
    one_week_ago = now - datetime.timedelta(days=7)
    
    # Only count timestamps from the past week
    recent_timestamps = [ts for ts in timestamps if ts > one_week_ago]
    
    # Clean up old timestamps (optional, keeps the file from growing indefinitely)
    if len(recent_timestamps) < len(timestamps):
        usage_data["timestamps"] = [ts.isoformat() for ts in recent_timestamps]
        save_usage_data(usage_data)
    
    return len(recent_timestamps)

def update_usage(num_invoices):
    """Update the global usage with new invoices"""
    usage_data = load_usage_data()
    now = datetime.datetime.now()
    
    # Add new timestamps (one for each invoice)
    for _ in range(num_invoices):
        usage_data.setdefault("timestamps", []).append(now.isoformat())
    
    save_usage_data(usage_data)

def extract_images_from_pdf(pdf_files, max_images=MAX_INVOICE_IMAGES):
    """Extract images from multiple PDF files, up to the max limit"""
    all_images = []
    
    for pdf_file in pdf_files:
        # Store the original position to reset later
        pdf_file.seek(0)
        
        # Check if we've already reached the maximum
        if len(all_images) >= max_images:
            break
            
        # Open the PDF file
        pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
        
        # Iterate through each page
        for page_num in range(len(pdf_document)):
            if len(all_images) >= max_images:
                break
                
            page = pdf_document[page_num]
            image_list = page.get_images(full=True)
            
            # Extract each image
            for img_index, img in enumerate(image_list):
                if len(all_images) >= max_images:
                    break
                    
                xref = img[0]
                base_image = pdf_document.extract_image(xref)
                image_bytes = base_image["image"]
                
                # Convert to PIL Image
                image = Image.open(io.BytesIO(image_bytes))
                all_images.append({"image": image, "pdf_name": pdf_file.name})
        
        pdf_document.close()
        
    return all_images

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
    
    # Check weekly usage
    weekly_usage = get_weekly_usage_count()
    remaining_weekly_limit = MAX_INVOICES_PER_WEEK - weekly_usage
    
    # Display current usage information and time of data
    st.write("Upload PDFs containing invoice images, and get an Excel sheet with the extracted data.")
    
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"Limitation: Maximum {MAX_INVOICE_IMAGES} invoice images per session")
    with col2:
        if remaining_weekly_limit <= 0:
            st.error(f"Weekly limit reached: {weekly_usage}/{MAX_INVOICES_PER_WEEK} invoices processed this week.")
        else:
            st.info(f"Weekly usage: {weekly_usage}/{MAX_INVOICES_PER_WEEK} invoices processed this week")
    
    # Current week info
    current_time = datetime.datetime.now()
    week_start = current_time - datetime.timedelta(days=7)
    st.caption(f"Current weekly period: {week_start.strftime('%Y-%m-%d')} to {current_time.strftime('%Y-%m-%d')}")
    
    # File uploader
    uploaded_files = st.file_uploader("Upload PDF files", type="pdf", accept_multiple_files=True)
    
    if uploaded_files and remaining_weekly_limit > 0:
        if st.button("Extract Data"):
            # Extract all images first (limited to MAX_INVOICE_IMAGES)
            image_data = extract_images_from_pdf(uploaded_files, MAX_INVOICE_IMAGES)
            
            if not image_data:
                st.warning("No images found in the uploaded PDFs.")
                return
                
            st.info(f"Found {len(image_data)} invoice images. Processing...")
            
            # Check if processing these would exceed weekly limit
            if weekly_usage + len(image_data) > MAX_INVOICES_PER_WEEK:
                actual_process_count = MAX_INVOICES_PER_WEEK - weekly_usage
                st.warning(f"Processing only {actual_process_count} invoices to stay within the weekly limit of {MAX_INVOICES_PER_WEEK}.")
                image_data = image_data[:actual_process_count]
            
            all_invoice_data = []
            progress_bar = st.progress(0)
            
            for i, img_info in enumerate(image_data):
                image = img_info["image"]
                pdf_name = img_info["pdf_name"]
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.image(image, caption=f"Image {i+1} from {pdf_name}", width=300)
                
                with col2:
                    # Extract the data
                    raw_data = extract_invoice_data(image)
                    
                    # Display as an image to prevent copying
                    st.markdown(f"**Extracted Data {i+1}:**")
                    json_image = text_to_image(raw_data)
                    st.image(json_image, caption="Preview only - copying disabled")
                    
                    # Parse JSON from the extracted text
                    parsed_data = parse_json_from_text(raw_data)
                    
                    if parsed_data:
                        # Extract the main fields
                        invoice_entry = {
                            "PDF_File": pdf_name,
                            "Invoice_Number": parsed_data.get("Invoice Number", ""),
                            "Invoice_Date": parsed_data.get("Invoice Date", ""),
                            "Vendor_Name": parsed_data.get("Vendor/Company Name", ""),
                            "Total_Amount": parsed_data.get("Total Amount", "")
                        }
                        
                        # Handle items differently - flatten them for Excel
                        items = parsed_data.get("Items", [])
                        invoice_entry["Items_Summary"] = flatten_items(items)
                        
                        all_invoice_data.append(invoice_entry)
                    else:
                        st.warning(f"Could not parse JSON data from extraction {i+1}")
                
                # Update progress
                progress_bar.progress((i + 1) / len(image_data))
            
            # Update global usage with the number of processed invoices
            update_usage(len(all_invoice_data))
            
            # Update weekly usage display - use st.rerun() instead of st.experimental_rerun()
            st.rerun()
            
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
    elif remaining_weekly_limit <= 0:
        st.error("The weekly limit has been reached. Please try again next week.")

if __name__ == "__main__":
    main()
