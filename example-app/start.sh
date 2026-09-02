#!/bin/bash

# Start the FastAPI app in the background
python app.py &

# Start gotty terminal in the foreground
gotty -w -p 7681 bash
