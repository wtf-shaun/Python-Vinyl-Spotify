const CLIENT_ID = "f9b69808a4f64f299c26b42bbe1c80ef";
const REDIRECT_URI = window.location.origin + window.location.pathname;
const SCOPES = "user-read-currently-playing user-read-playback-state";
const API_BASE = "https://api.spotify.com/v1";

const $ = (selector) => document.querySelector(selector);
const state = { token: sessionStorage.getItem("airwave_token"), expiresAt: Number(sessionStorage.getItem("airwave_expires") || 0), verifier: null, lastTrack: null, progress: 0, duration: 0, isPlaying: false, timer: null };

function base64Url(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
async function sha256(value) { return crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)); }
function randomString(length) { const bytes = new Uint8Array(length); crypto.getRandomValues(bytes); return base64Url(bytes); }

async function connect() {
  if (CLIENT_ID === "YOUR_SPOTIFY_CLIENT_ID") { setStatus("Add your Spotify Client ID in app.js", "error"); return; }
  state.verifier = randomString(64); sessionStorage.setItem("airwave_verifier", state.verifier);
  const challenge = base64Url(await sha256(state.verifier)); const authState = randomString(24); sessionStorage.setItem("airwave_state", authState);
  const params = new URLSearchParams({ client_id: CLIENT_ID, response_type: "code", redirect_uri: REDIRECT_URI, scope: SCOPES, state: authState, code_challenge_method: "S256", code_challenge: challenge });
  window.location.assign(`https://accounts.spotify.com/authorize?${params}`);
}

async function finishLogin() {
  const params = new URLSearchParams(window.location.search); const code = params.get("code");
  if (!code) return;
  if (params.get("state") !== sessionStorage.getItem("airwave_state")) throw new Error("Spotify authorization state did not match.");
  const verifier = sessionStorage.getItem("airwave_verifier"); const response = await fetch("https://accounts.spotify.com/api/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ client_id: CLIENT_ID, grant_type: "authorization_code", code, redirect_uri: REDIRECT_URI, code_verifier: verifier }) });
  if (!response.ok) throw new Error("Spotify could not finish the connection.");
  const data = await response.json(); storeToken(data); window.history.replaceState({}, "", REDIRECT_URI); window.location.hash = "connected";
}
function storeToken(data) { state.token = data.access_token; state.expiresAt = Date.now() + (data.expires_in * 1000) - 60000; sessionStorage.setItem("airwave_token", state.token); sessionStorage.setItem("airwave_expires", state.expiresAt); }
async function api(path) { if (!state.token || Date.now() > state.expiresAt) throw new Error("Your Spotify session expired. Reconnect to continue."); const response = await fetch(API_BASE + path, { headers: { Authorization: `Bearer ${state.token}` } }); if (response.status === 204) return null; if (response.status === 401) throw new Error("Your Spotify session expired. Reconnect to continue."); if (!response.ok) throw new Error("Spotify playback is unavailable right now."); return response.json(); }

async function refreshPlayback() {
  if (!state.token) return; setStatus("Scanning the airwaves...");
  try { const [playing, queue] = await Promise.all([api("/me/player/currently-playing"), api("/me/player/queue")]); updateTrack(playing); updateQueue(queue); } catch (error) { setStatus(error.message, "error"); }
}
function updateTrack(data) {
  if (!data || !data.item || data.item.type !== "track") { $("#track-title").textContent = "Nothing is playing yet"; $("#track-artist").textContent = "Start a song in Spotify and refresh the dial."; $("#track-album").textContent = ""; setStatus("Waiting for a signal"); return; }
  const track = data.item; state.progress = data.progress_ms || 0; state.duration = track.duration_ms || 0; state.isPlaying = Boolean(data.is_playing); state.lastTrack = track.id; $("#track-title").textContent = track.name; $("#track-artist").textContent = track.artists.map((artist) => artist.name).join(", "); $("#track-album").textContent = track.album.name; $("#device-name").textContent = data.device?.name || "Spotify device"; $("#album-art").innerHTML = track.album.images?.[0] ? `<img src="${track.album.images[0].url}" alt="${track.album.name} album artwork">` : '<span class="album-placeholder">&#9835;</span>'; $("#connection-label").textContent = "ON AIR"; document.body.classList.add("connected"); setStatus(state.isPlaying ? "Now transmitting" : "Paused in Spotify", "success"); updateProgress(); }
function updateQueue(data) { const item = data?.queue?.[0]; $("#next-track").textContent = item ? `Next up: ${item.name} / ${item.artists?.[0]?.name || "Unknown artist"}` : "Next up: queue is empty"; }
function updateProgress() { const percent = state.duration ? Math.min(100, (state.progress / state.duration) * 100) : 0; $("#progress-bar").style.width = `${percent}%`; $("#track-time").textContent = `${formatTime(state.progress)} / ${formatTime(state.duration)}`; }
function formatTime(ms) { const seconds = Math.floor(ms / 1000); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; }
function setStatus(message, type = "idle") { $("#status-message").textContent = message; $("#status-message").style.color = type === "error" ? "var(--rust-dark)" : type === "success" ? "var(--green)" : ""; }
function tick() { if (state.isPlaying && state.progress < state.duration) { state.progress += 250; updateProgress(); } }

$("#connect-button").addEventListener("click", connect); $("#refresh-button").addEventListener("click", refreshPlayback); $("#pause-button").addEventListener("click", () => { document.body.classList.toggle("paused"); $("#pause-button").setAttribute("aria-label", document.body.classList.contains("paused") ? "Resume animation" : "Pause animation"); });
setInterval(tick, 250); setInterval(refreshPlayback, 5000);
(async function init() { try { await finishLogin(); } catch (error) { setStatus(error.message, "error"); } if (state.token) { $("#connect-button").textContent = "Spotify connected"; $("#setup-note").textContent = "Your session is stored only in this browser tab."; refreshPlayback(); } })();