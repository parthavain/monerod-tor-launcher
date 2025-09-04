# 🧅 Monerod TOR Launcher (Windows)

A simple Windows program to run **Monero's monerod daemon** anonymously through the **Tor network**, with a password-protected web dashboard.

## 🚀 How to Use (Windows)
1. Copy this launcher program into the same folder where your **`monerod.exe`** is located.  
2. Make sure **`tor.exe`** is in the same folder (it will be provided with the package).  
3. Run the launcher `.exe`.  
4. On the first start, you will be asked to set a **master password** (used to access the web dashboard).  
5. The program will then:
   - Start the **Tor service**  
   - Launch **Monerod** with Tor proxy integration  
   - Open a local web dashboard → `http://127.0.0.1:8080`  
   - Generate a **.onion address** for anonymous access through Tor  

## ✨ Features
- Run Monerod anonymously via Tor  
- Password-protected web dashboard  
- Displays:
  - Blockchain height  
  - Synchronization status  
  - Mining status & hash rate  
  - Network connections  
  - Onion address  
- Real-time log viewer (Tor & Monerod)  

## ⚠️ Note
- This software is **experimental**.  

