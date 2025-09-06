# Stock Management System

This document describes how to use the stock management system for importing and managing inventory data.

## Overview

The stock management system provides:
1. **Stock Loader Script** - Import stock data from Google Sheets CSV export
2. **Stock Management API** - Endpoints for updating stock levels
3. **Stock Management UI** - Web interface for managing inventory

## 1. Loading Stock Data from Google Sheets

### Step 1: Export Google Sheets Data
1. Open your Google Sheets inventory file
2. Go to File → Download → Comma Separated Values (.csv)
3. Save the file to your computer

### Step 2: Run the Stock Loader Script

```bash
# Navigate to the impag-quot directory
cd impag-quot

# Run the stock loader script
python stock_loader.py path/to/your/stock_data.csv
```

### CSV Format Expected

The script expects a CSV with the following columns:
- **Material** - Product name
- **Unidad** - Unit (PIEZA, ROLLO, METRO, KG, etc.)
- **Cantidad Compradas** - Quantity purchased
- **Cantidad en Stock** - Current stock quantity
- **Cantidad Vendidas** - Quantity sold
- **Costo Unitario** - Unit cost (with $ symbol, e.g., $123.45)
- **Importe** - Total amount
- **COMENTARIO** - Comments
- **Fecha Primer Actualizacion** - First update date (DD/MM/YYYY)
- **Fecha de Ultima Actualizacion** - Last update date (DD/MM/YYYY)

### What the Script Does

- **Creates new products** if they don't exist in the database
- **Updates existing products** with new stock levels and prices
- **Generates unique SKUs** based on product names
- **Handles currency parsing** (removes $ symbols and commas)
- **Parses dates** in DD/MM/YYYY format
- **Maps units** to standard enum values
- **Provides detailed output** showing created/updated counts

### Example Output

```
Row 2: Updated PROTECTOR DE CULTIVO CLIP PLUS CAJA 10000 PZ (POPUSA) - Stock: 7492, Price: 0.11
Row 3: Created Gancho de tutorado alambre galv 20cm (INVERTEX) - SKU: GANCHO-DE-TUTORADO-ALAMBRE-GALV-20CM-INVERTEX, Stock: 363, Price: 1.60

=== IMPORT SUMMARY ===
Products created: 15
Products updated: 125
Errors: 0
Total processed: 140
```

## 2. Stock Management API Endpoints

### Get Products in Stock
```
GET /products/stock?min_stock=1&limit=100
```

### Update Single Product Stock
```
PATCH /products/{product_id}/stock?stock=50&price=25.99
```

### Bulk Update Stock
```
POST /products/stock/bulk-update
{
  "updates": [
    {"product_id": 1, "stock": 100, "price": 25.50},
    {"product_id": 2, "stock": 50}
  ]
}
```

### Filter Products by Stock Level
```
GET /products?min_stock=1         # Products with at least 1 in stock
GET /products?max_stock=0         # Products out of stock
GET /products?max_stock=9         # Products with low stock (≤ 9)
```

## 3. Stock Management UI

### Accessing the Stock Management Page
1. Navigate to your admin app
2. Click on "Inventario" in the navigation
3. The page shows all products with current stock levels

### Features
- **Real-time editing** - Click edit icon to modify stock and price
- **Search and filter** - Find products by name or SKU
- **Stock status filtering** - Show only in-stock, out-of-stock, or low-stock items
- **Summary statistics** - Total products, products with stock, total inventory value
- **Quick updates** - Inline editing with immediate save

### Using Stock Filters in Products Page
The main Products page now includes a stock filter with options:
- **Todos los Productos** - Show all products
- **Solo con Stock** - Show only products with stock > 0
- **Sin Stock** - Show only products with stock = 0
- **Stock Bajo** - Show only products with stock < 10

## 4. Automation Workflow

### Recommended Process
1. **Weekly/Monthly**: Export your Google Sheets inventory
2. **Run the loader script** to update the database
3. **Use the Stock Management UI** for daily adjustments
4. **Monitor stock levels** using the filters and dashboard

### Benefits
- **Centralized inventory** in your database
- **Easy stock updates** through the web interface
- **Better inventory tracking** with filters and search
- **Automated SKU generation** for new products
- **Price tracking** with last update timestamps

## 5. Troubleshooting

### Common Issues

**"Product with this SKU already exists"**
- The script tries to create a unique SKU but found a duplicate
- This usually happens if the same product appears multiple times in the CSV

**"Error parsing currency"**
- Check that currency values use $ symbol and proper decimal format
- Example: $123.45 (correct) vs 123,45 (incorrect)

**"Error parsing date"**
- Ensure dates are in DD/MM/YYYY format
- Empty dates are acceptable and will be ignored

**"Database connection error"**
- Check that your database is running and accessible
- Verify the database_url in your config

### Getting Help
Check the script output for detailed error messages. Each row that fails will show the specific error and line number.

