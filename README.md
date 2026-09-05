# Spotify Vinyl — Tkinter Desktop App

A Python/Tkinter desktop app that connects to Spotify using OAuth 2.0 Authorization Code with PKCE, displays the currently playing song, downloads its album artwork, turns it into a vinyl record and continuously rotates it.

## Requirements

- Python 3.10+
- Spotify account with Web API access
- Internet connection
- Windows/macOS/Linux

## 1. Create the Spotify app

Open:

https://developer.spotify.com/dashboard

Create a new app and select **Web API**.

In the app settings, add this Redirect URI:

http://127.0.0.1:8888/callback

The URI must match what the program sends to Spotify.

## 2. Install dependencies

Open a terminal in this folder:

Windows:

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements.txt

macOS/Linux:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

## 3. Configure the Client ID

Copy:

    .env.example

to:

    .env

Then put your Spotify Client ID into it:

    SPOTIFY_CLIENT_ID=your_client_id_here

Do NOT put your Spotify password into the program.

This desktop app uses PKCE, so a Spotify Client Secret is not required.

## 4. Run

    python main.py

Click **Connect Spotify**.

Your browser will open Spotify's authorization page. Log in and approve the requested permissions.

After approval, return to the Tkinter app.

## What it does

- Spotify OAuth login
- PKCE authentication
- Reads the currently playing track
- Displays title, artist and album
- Displays playback progress
- Shows active Spotify device
- Downloads the album cover
- Creates a circular vinyl record from the artwork
- Adds vinyl grooves and a center hole
- Adds a pulsing ambient glow based on the album artwork colors
- Displays the vinyl on a white turntable with a tonearm
- Smoothly rotates the record at 60 FPS
- Pauses and resumes vinyl animation
- Manually refreshes playback without waiting for the next poll
- Updates playback progress locally between Spotify polls
- Reads and displays the next song in Spotify's queue
- Automatically detects when the song changes
- Refreshes the Spotify token when necessary

## Troubleshooting

### "Redirect URI mismatch"

Make sure the Spotify Dashboard contains exactly:

http://127.0.0.1:8888/callback

Do not replace `127.0.0.1` with `localhost`.

### "Nothing is currently playing"

Start playing a song in Spotify and wait a few seconds.

### Port 8888 is already in use

Close the program using port 8888, then restart this application.

If you want to change the port, change both:

    REDIRECT_URI = "http://127.0.0.1:8888/callback"

and:

    HTTPServer(("127.0.0.1", 8888), CallbackHandler)

to the same new port, then update the Redirect URI in Spotify Dashboard.

### Album art does not load

Check your internet connection and make sure Spotify returned album artwork for the track.

## Security

The application uses OAuth Authorization Code with PKCE. It does not ask for or store your Spotify password.

The `.env` file is ignored by Git through `.gitignore`.

Only the following read-only scopes are requested:

- user-read-currently-playing
- user-read-playback-state
