"""Simple FastAPI CRUD app with SQLite."""

import random
import sqlite3
import string
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

app = FastAPI(title="Items Manager")

# Add CORS middleware to allow API calls from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods
    allow_headers=["*"],  # Allow all headers
)

DB_PATH = Path(__file__).parent / "items.db"


class Item(BaseModel):
    """An item with optional name."""

    name: str | None = None


class ItemResponse(BaseModel):
    """Item with ID."""

    id: int
    name: str


def init_db():
    """Initialize the database."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def generate_random_name(length: int = 10) -> str:
    """Generate a random lowercase name."""
    return "".join(random.choices(string.ascii_lowercase, k=length))


@app.on_event("startup")
async def startup():
    """Initialize database on startup."""
    init_db()


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the frontend HTML."""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Items Manager</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
            background: #f5f5f5;
        }
        h1 {
            color: #333;
        }
        .controls {
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        input {
            padding: 10px;
            font-size: 14px;
            border: 1px solid #ddd;
            border-radius: 4px;
            width: 300px;
        }
        button {
            padding: 10px 20px;
            font-size: 14px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-left: 10px;
        }
        button:hover {
            background: #0056b3;
        }
        button.danger {
            background: #dc3545;
        }
        button.danger:hover {
            background: #c82333;
        }
        #items {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .item {
            padding: 10px;
            border-bottom: 1px solid #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .item:last-child {
            border-bottom: none;
        }
        .item-info {
            flex: 1;
        }
        .item-id {
            color: #666;
            font-size: 12px;
            margin-right: 10px;
        }
        .item-name {
            font-weight: 500;
        }
        .empty {
            color: #999;
            text-align: center;
            padding: 40px;
        }
    </style>
</head>
<body>
    <h1>Items Manager</h1>

    <div class="controls">
        <input type="text" id="nameInput" placeholder="Item name (leave empty for random)" />
        <button onclick="addItem()">Add Item</button>
        <button onclick="listItems()">Refresh List</button>
        <button class="danger" onclick="deleteAll()">Delete All</button>
    </div>

    <div id="items"></div>

    <script>
        async function addItem() {
            const name = document.getElementById('nameInput').value.trim();
            const response = await fetch('/items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name || null })
            });
            if (response.ok) {
                document.getElementById('nameInput').value = '';
                await listItems();
            }
        }

        async function deleteItem(id) {
            const response = await fetch(`/items/${id}`, { method: 'DELETE' });
            if (response.ok) {
                await listItems();
            }
        }

        async function deleteAll() {
            if (!confirm('Delete all items?')) return;
            const response = await fetch('/items', { method: 'DELETE' });
            if (response.ok) {
                await listItems();
            }
        }

        async function listItems() {
            const response = await fetch('/items');
            const items = await response.json();

            const container = document.getElementById('items');
            if (items.length === 0) {
                container.innerHTML = '<div class="empty">No items yet. Add one above!</div>';
            } else {
                container.innerHTML = items.map(item => `
                    <div class="item">
                        <div class="item-info">
                            <span class="item-id">#${item.id}</span>
                            <span class="item-name">${item.name}</span>
                        </div>
                        <button class="danger" onclick="deleteItem(${item.id})">Delete</button>
                    </div>
                `).join('');
            }
        }

        // Load items on page load
        listItems();
    </script>
</body>
</html>
    """


@app.post("/items", response_model=ItemResponse)
async def create_item(item: Item):
    """Create a new item."""
    name = item.name if item.name else generate_random_name()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO items (name) VALUES (?)", (name,))
    item_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return ItemResponse(id=item_id, name=name)


@app.get("/items", response_model=list[ItemResponse])
async def list_items():
    """List all items."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM items ORDER BY id")
    rows = cursor.fetchall()
    conn.close()

    return [ItemResponse(id=row[0], name=row[1]) for row in rows]


@app.delete("/items/{item_id}")
async def delete_item(item_id: int):
    """Delete an item by ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM items WHERE id = ?", (item_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        raise HTTPException(status_code=404, detail="Item not found")

    return {"message": "Item deleted"}


@app.delete("/items")
async def delete_all_items():
    """Delete all items."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM items")
    conn.commit()
    conn.close()

    return {"message": "All items deleted"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
