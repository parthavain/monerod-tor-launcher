# 🧅 Monerod TOR Launcher (Windows)

A simple Windows program to run **Monero's monerod daemon** anonymously through the **Tor network**, with a password-protected web dashboard.

## 🚀 Usage (Windows)
1. Download the compiled `.exe` file from Releases.
2. Place the file in the same folder as:
   - `monerod.exe`
   - `tor.exe`
3. Run the launcher `.exe`
4. On first start, set a **master password** (used to protect web access).
5. The program will:
   - Start **Tor**
   - Start **Monerod** with Tor proxy integration
   - Launch a **local web dashboard** at:  
     👉 `http://127.0.0.1:8080`  
   - Provide a **.onion address** for anonymous access.

## ✨ Features
- Runs monerod anonymously via Tor
- Web interface with password protection
- Shows:
  - Blockchain height  
  - Sync status  
  - Peer connections  
  - Onion address  
- Real-time Tor & Monerod log viewer

## ⚠️ Note
- This program is experimental.  
- Use only for **educational or personal purposes**.  

