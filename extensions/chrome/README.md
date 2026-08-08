# Auren Page Reader (Chrome)

Sends the **full extracted text** of the active tab to the Auren API so the voice
agent can explain the article — including content below the fold — without
relying on screenshots.

## Install (unpacked)

1. Open `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → select this folder (`extensions/chrome`)
4. Open the extension options and confirm API base URL (`http://127.0.0.1:8080` for local)

## Use

1. Open an article tab
2. Click the extension → **Send page to Auren** (or `Alt+Shift+A`)
3. Start a voice session on `/talk`
4. Ask: “Explain this article” / “Summarise the page I just sent”

## Auth

- **Local API with `DEV_USER_ID` and no Clerk**: leave Bearer empty
- **Clerk-enabled API**: paste a session JWT into the extension settings

Page context expires after 30 minutes and is not stored as durable memory.
