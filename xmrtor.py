import os
import subprocess
import threading
import time
import json
import re
import hashlib
import getpass
from datetime import datetime
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from werkzeug.serving import make_server
import requests

# ========== Security Functions ==========
def hash_password(password):
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def get_secure_password():
    """Get password from user with confirmation"""
    print("\n" + "="*60)
    print("🔐 SECURITY SETUP")
    print("="*60)
    print("Please set a secure password for web interface access.")
    print("This password will protect your monerod dashboard.")
    print("="*60)
    
    while True:
        password = getpass.getpass("🔑 Enter master password: ")
        if len(password) < 8:
            print("❌ Password must be at least 8 characters long!")
            continue
            
        confirm = getpass.getpass("🔑 Confirm password: ")
        if password != confirm:
            print("❌ Passwords do not match! Please try again.")
            continue
            
        print("✅ Password set successfully!")
        return hash_password(password)

# ========== Configuration ==========
BASE_DIR = os.getcwd()
TOR_EXE = os.path.join(BASE_DIR, "tor.exe")
TOR_DATA_DIR = os.path.join(BASE_DIR, "tor_data")
TORRC_PATH = os.path.join(BASE_DIR, "torrc")
MONEROD_EXE = os.path.join(BASE_DIR, "monerod.exe")
HOSTNAME_PATH = os.path.join(TOR_DATA_DIR, "hostname")
SOCKS_PORT = 9050
HIDDEN_SERVICE_PORT = 18081
HIDDEN_SERVICE_WEB_PORT = 80
LOCAL_PORT = 18081
WEB_PORT = 8080


tor_process = None
monerod_process = None
web_server = None
server_thread = None
master_password = None 
app_status = {
    'tor_running': False,
    'monerod_running': False,
    'onion_address': 'Waiting...',
    'status': 'Ready',
    'tor_logs': [],
    'monerod_logs': [],
    'block_height': 0,
    'sync_status': 'Not synced',
    'mining_status': 'Not mining',
    'hash_rate': '0 H/s',
    'connections': 0,
    'uptime': '00:00:00'
}

start_time = None


app = Flask(__name__)
app.secret_key = os.urandom(24)  

def check_auth():
    """Check if user is authenticated"""
    return session.get('authenticated', False)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        if password and hash_password(password) == master_password:
            session['authenticated'] = True
            session.permanent = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid password!')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    if not check_auth():
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    if not check_auth():
        return jsonify({'error': 'Not authenticated'}), 401
    global start_time
    if start_time and app_status['monerod_running']:
        uptime = str(datetime.now() - start_time).split('.')[0]
        app_status['uptime'] = uptime
    return jsonify(app_status)

@app.route('/api/start', methods=['POST'])
def start_services():
    if not check_auth():
        return jsonify({'error': 'Not authenticated'}), 401
    threading.Thread(target=start_all_services, daemon=True).start()
    return jsonify({'message': 'Starting services...'})

@app.route('/api/logs/<service>')
def get_logs(service):
    if not check_auth():
        return jsonify({'error': 'Not authenticated'}), 401
    if service == 'tor':
        return jsonify({'logs': app_status['tor_logs'][-100:]})  # Last 100 logs
    elif service == 'monerod':
        return jsonify({'logs': app_status['monerod_logs'][-100:]})
    return jsonify({'logs': []})


def log_message(service, message):
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = f"[{timestamp}] {message}"
    
    if service == 'tor':
        app_status['tor_logs'].append(log_entry)
        if len(app_status['tor_logs']) > 10:  # Keep only last 500 logs
            app_status['tor_logs'] = app_status['tor_logs'][-500:]
    elif service == 'monerod':
        app_status['monerod_logs'].append(log_entry)
        if len(app_status['monerod_logs']) > 10:
            app_status['monerod_logs'] = app_status['monerod_logs'][-500:]

def write_torrc():
    if not os.path.exists(TOR_DATA_DIR):
        os.makedirs(TOR_DATA_DIR)
    
    with open(TORRC_PATH, "w", encoding="utf-8") as f:
        f.write(f"""
SocksPort {SOCKS_PORT}
HiddenServiceDir {TOR_DATA_DIR}
HiddenServicePort {HIDDEN_SERVICE_PORT} 127.0.0.1:{LOCAL_PORT}
HiddenServicePort {HIDDEN_SERVICE_WEB_PORT} 127.0.0.1:{WEB_PORT}
Log notice stdout
AvoidDiskWrites 1
""")

def start_tor():
    global tor_process
    log_message('tor', 'Starting TOR service...')
    write_torrc()
    
    tor_process = subprocess.Popen(
        [TOR_EXE, "-f", TORRC_PATH],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    app_status['tor_running'] = True
    threading.Thread(target=read_tor_logs, daemon=True).start()

def read_tor_logs():
    while tor_process and tor_process.poll() is None:
        try:
            line = tor_process.stdout.readline()
            if line:
                log_message('tor', line.strip())
                if "100%" in line and "bootstrapped" in line.lower():
                    app_status['status'] = 'TOR network connected'
        except:
            break

def wait_onion_address(timeout=60):
    log_message('tor', 'Waiting for .onion address generation...')
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        if os.path.exists(HOSTNAME_PATH):
            try:
                with open(HOSTNAME_PATH, "r", encoding="utf-8") as f:
                    onion = f.read().strip()
                    if onion:
                        return onion
            except:
                pass
        time.sleep(1)
    return None

def start_monerod():
    global monerod_process, start_time
    log_message('monerod', 'Starting monerod daemon...')
    
    args = [
        MONEROD_EXE,
        f"--proxy=127.0.0.1:{SOCKS_PORT}",
        "--hide-my-port",
        "--no-igd",
        "--confirm-external-bind",
        f"--p2p-bind-port={LOCAL_PORT}",
        f"--rpc-bind-port={LOCAL_PORT}",
        "--rpc-bind-ip=127.0.0.1",
        
        "--enable-dns-blocklist"
        
        
    ]
    
    monerod_process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    app_status['monerod_running'] = True
    start_time = datetime.now()
    threading.Thread(target=read_monerod_logs, daemon=True).start()
    threading.Thread(target=monitor_monerod_status, daemon=True).start()

def read_monerod_logs():
    while monerod_process and monerod_process.poll() is None:
        try:
            line = monerod_process.stdout.readline()
            if line:
                log_message('monerod', line.strip())
                parse_monerod_log(line.strip())
        except:
            break

def parse_monerod_log(log_line):
    # Parse block height
    height_match = re.search(r'Height: (\d+)', log_line)
    if height_match:
        app_status['block_height'] = int(height_match.group(1))
    
    # Parse sync status
    if "Synced" in log_line:
        app_status['sync_status'] = 'Synced'
        app_status['status'] = 'Fully synchronized'
    elif "synchronizing" in log_line.lower():
        app_status['sync_status'] = 'Synchronizing'
        app_status['status'] = 'Synchronizing blockchain...'
    
    # Parse mining status
    if "mining" in log_line.lower() and "started" in log_line.lower():
        app_status['mining_status'] = 'Mining active'
    elif "mining" in log_line.lower() and ("stopped" in log_line.lower() or "paused" in log_line.lower()):
        app_status['mining_status'] = 'Mining stopped'
    
    # Parse hash rate
    hash_match = re.search(r'(\d+\.?\d*)\s*H/s', log_line)
    if hash_match:
        app_status['hash_rate'] = f"{hash_match.group(1)} H/s"

def monitor_monerod_status():
    while app_status['monerod_running']:
        try:
            # Try to get info from monerod RPC
            response = requests.post(
                f'http://127.0.0.1:{LOCAL_PORT}/json_rpc',
                json={
                    "jsonrpc": "2.0",
                    "id": "0",
                    "method": "get_info"
                },
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'result' in data:
                    result = data['result']
                    app_status['block_height'] = result.get('height', app_status['block_height'])
                    app_status['connections'] = result.get('outgoing_connections_count', 0) + result.get('incoming_connections_count', 0)
                    
                    if result.get('synchronized', False):
                        app_status['sync_status'] = 'Synced'
                    else:
                        app_status['sync_status'] = 'Synchronizing'
                        
        except:
            pass  # Ignore RPC errors
        
        time.sleep(30)  # Check every 30 seconds

def start_all_services():
    try:
        app_status['status'] = 'Starting TOR...'
        start_tor()
        
        app_status['status'] = 'Waiting for .onion address...'
        onion = wait_onion_address()
        
        if not onion:
            app_status['status'] = 'Failed to get .onion address'
            log_message('tor', 'ERROR: Failed to generate .onion address')
            return
        
        app_status['onion_address'] = f"{onion}:{HIDDEN_SERVICE_PORT}"
        log_message('tor', f'Onion address generated: {onion}')
        
        app_status['status'] = 'Waiting for TOR network stability...'
        time.sleep(15)  # Wait for TOR network stability
        
        app_status['status'] = 'Starting monerod...'
        start_monerod()
        
        app_status['status'] = 'Services running anonymously via TOR'
        log_message('monerod', 'monerod is now running anonymously through TOR network')
        
    except Exception as e:
        app_status['status'] = f'Error: {str(e)}'
        log_message('monerod', f'ERROR: {str(e)}')

def start_web_server():
    global web_server, server_thread
    
    # Create templates directory and HTML files
    os.makedirs('templates', exist_ok=True)
    
    # Login page HTML
    login_html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🧅 Secure Access - Monerod TOR Launcher</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: #ffffff;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .login-container {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 25px;
            padding: 50px;
            width: 100%;
            max-width: 450px;
            text-align: center;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.3);
            animation: fadeInUp 1s ease-out;
        }

        .login-header {
            margin-bottom: 40px;
        }

        .login-header h1 {
            font-size: 2.5rem;
            background: linear-gradient(45deg, #00ff88, #00ccff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 15px;
            text-shadow: 0 0 30px rgba(0, 255, 136, 0.3);
        }

        .login-header .subtitle {
            color: #8892b0;
            font-size: 1.1rem;
            margin-bottom: 10px;
        }

        .security-notice {
            background: rgba(255, 193, 7, 0.1);
            border: 1px solid rgba(255, 193, 7, 0.3);
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
            color: #ffc107;
            font-size: 0.9rem;
        }

        .form-group {
            margin-bottom: 30px;
            text-align: left;
        }

        .form-group label {
            display: block;
            color: #8892b0;
            margin-bottom: 8px;
            font-weight: 500;
        }

        .form-control {
            width: 100%;
            padding: 15px 20px;
            background: rgba(255, 255, 255, 0.08);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 12px;
            color: #ffffff;
            font-size: 1.1rem;
            transition: all 0.3s ease;
        }

        .form-control:focus {
            outline: none;
            border-color: #00ff88;
            box-shadow: 0 0 20px rgba(0, 255, 136, 0.2);
            background: rgba(255, 255, 255, 0.12);
        }

        .btn-login {
            width: 100%;
            background: linear-gradient(45deg, #00ff88, #00ccff);
            border: none;
            color: #1a1a2e;
            padding: 15px 30px;
            font-size: 1.1rem;
            font-weight: 600;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 30px rgba(0, 255, 136, 0.3);
        }

        .error-message {
            background: rgba(220, 53, 69, 0.1);
            border: 1px solid rgba(220, 53, 69, 0.3);
            color: #dc3545;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            font-weight: 500;
        }

        .creator {
            margin-top: 30px;
            color: #64ffda;
            font-size: 0.9rem;
            opacity: 0.8;
        }

        .creator a {
            color: #64ffda;
            text-decoration: none;
            transition: all 0.3s ease;
        }

        .creator a:hover {
            color: #00ff88;
            text-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
        }

        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .lock-icon {
            font-size: 4rem;
            color: #00ff88;
            margin-bottom: 20px;
            text-shadow: 0 0 20px rgba(0, 255, 136, 0.4);
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="lock-icon">🔐</div>
        
        <div class="login-header">
            <h1>🧅 Secure Access</h1>
            <div class="subtitle">Monerod TOR Anonymous Launcher</div>
        </div>

        <div class="security-notice">
            <strong>🛡️ Security Notice:</strong> This interface is protected to ensure your privacy and security.
        </div>

        {% if error %}
        <div class="error-message">
            ❌ {{ error }}
        </div>
        {% endif %}

        <form method="POST">
            <div class="form-group">
                <label for="password">🔑 Master Password:</label>
                <input type="password" id="password" name="password" class="form-control" 
                       placeholder="Enter your secure password" required autofocus>
            </div>

            <button type="submit" class="btn-login">
                🚀 Access Dashboard
            </button>
        </form>

        <div class="creator">
            Created by <a href="https://x.com/parthavain" target="_blank">@parthavain</a>
        </div>
    </div>

    <script>
        // Auto-focus password field
        document.getElementById('password').focus();
        
        // Add enter key support
        document.getElementById('password').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.querySelector('.btn-login').click();
            }
        });
    </script>
</body>
</html>'''

    with open('templates/login.html', 'w', encoding='utf-8') as f:
        f.write(login_html)
    
    
    html_content = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monerod TOR Anonymous Launcher</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: #ffffff;
            min-height: 100vh;
            overflow-x: hidden;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
        }

        .header {
            text-align: center;
            margin-bottom: 40px;
            animation: fadeInDown 1s ease-out;
        }

        .header h1 {
            font-size: 2.8rem;
            background: linear-gradient(45deg, #00ff88, #00ccff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
            text-shadow: 0 0 30px rgba(0, 255, 136, 0.3);
        }

        .header .subtitle {
            color: #8892b0;
            font-size: 1.1rem;
            margin-bottom: 5px;
        }

        .creator {
            color: #64ffda;
            font-size: 0.9rem;
            opacity: 0.8;
        }

        .creator a {
            color: #64ffda;
            text-decoration: none;
            transition: all 0.3s ease;
        }

        .creator a:hover {
            color: #00ff88;
            text-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
        }

        .status-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 25px;
            margin-bottom: 40px;
        }

        .status-card {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px;
            padding: 25px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .status-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, #00ff88, #00ccff);
            opacity: 0;
            transition: opacity 0.3s ease;
        }

        .status-card:hover {
            transform: translateY(-5px);
            border-color: rgba(0, 255, 136, 0.3);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
        }

        .status-card:hover::before {
            opacity: 1;
        }

        .card-header {
            display: flex;
            align-items: center;
            margin-bottom: 15px;
        }

        .card-icon {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: linear-gradient(45deg, #00ff88, #00ccff);
            display: flex;
            align-items: center;
            justify-content: center;
            margin-right: 15px;
            font-size: 1.2rem;
        }

        .card-title {
            font-size: 1.3rem;
            font-weight: 600;
        }

        .card-value {
            font-size: 1.8rem;
            font-weight: bold;
            color: #00ff88;
            margin: 10px 0;
        }

        .card-description {
            color: #8892b0;
            font-size: 0.9rem;
        }

        .controls {
            text-align: center;
            margin-bottom: 40px;
        }

        .btn-primary {
            background: linear-gradient(45deg, #00ff88, #00ccff);
            border: none;
            color: #1a1a2e;
            padding: 15px 40px;
            font-size: 1.1rem;
            font-weight: 600;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 30px rgba(0, 255, 136, 0.3);
        }

        .btn-primary:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }

        .btn-secondary {
            background: linear-gradient(45deg, #ff4757, #ff6b7a);
            border: none;
            color: #ffffff;
            padding: 15px 30px;
            font-size: 1rem;
            font-weight: 600;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s ease;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .btn-secondary:hover {
            transform: translateY(-2px);
            box-shadow: 0 15px 30px rgba(255, 71, 87, 0.3);
        }

        .logs-container {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 25px;
            margin-top: 40px;
        }

        .log-panel {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 15px;
            overflow: hidden;
        }

        .log-header {
            background: rgba(0, 255, 136, 0.1);
            padding: 15px 20px;
            font-weight: 600;
            font-size: 1.1rem;
        }

        .log-content {
            height: 400px;
            overflow-y: auto;
            padding: 20px;
            font-family: 'Courier New', monospace;
            font-size: 0.85rem;
            line-height: 1.4;
        }

        .log-content::-webkit-scrollbar {
            width: 8px;
        }

        .log-content::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.1);
        }

        .log-content::-webkit-scrollbar-thumb {
            background: rgba(0, 255, 136, 0.3);
            border-radius: 4px;
        }

        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 8px;
        }

        .status-running {
            background: #00ff88;
            box-shadow: 0 0 10px rgba(0, 255, 136, 0.5);
            animation: pulse 2s infinite;
        }

        .status-stopped {
            background: #ff4757;
        }

        .status-waiting {
            background: #ffa502;
            animation: pulse 2s infinite;
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }

        @keyframes fadeInDown {
            from {
                opacity: 0;
                transform: translateY(-30px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        .onion-address {
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(0, 255, 136, 0.3);
            border-radius: 10px;
            padding: 15px;
            margin: 20px 0;
            font-family: monospace;
            word-break: break-all;
            color: #00ff88;
        }

        @media (max-width: 768px) {
            .logs-container {
                grid-template-columns: 1fr;
            }
            
            .header h1 {
                font-size: 2rem;
            }
            
            .status-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🧅 Monerod TOR Anonymous Launcher</h1>
            <div class="subtitle">Private & Anonymous Monero Daemon via TOR Network</div>
            <div class="creator">Created by <a href="https://x.com/parthavain" target="_blank">@parthavain</a></div>
        </div>

        <div class="status-grid">
            <div class="status-card">
                <div class="card-header">
                    <div class="card-icon">📊</div>
                    <div class="card-title">Block Height</div>
                </div>
                <div class="card-value" id="blockHeight">0</div>
                <div class="card-description">Current blockchain height</div>
            </div>

            <div class="status-card">
                <div class="card-header">
                    <div class="card-icon">🔄</div>
                    <div class="card-title">Sync Status</div>
                </div>
                <div class="card-value" id="syncStatus">Not synced</div>
                <div class="card-description">Blockchain synchronization</div>
            </div>

            

            

            <div class="status-card">
                <div class="card-header">
                    <div class="card-icon">🌐</div>
                    <div class="card-title">Connections</div>
                </div>
                <div class="card-value" id="connections">0</div>
                <div class="card-description">Network connections</div>
            </div>

            <div class="status-card">
                <div class="card-header">
                    <div class="card-icon">⏱️</div>
                    <div class="card-title">Uptime</div>
                </div>
                <div class="card-value" id="uptime">00:00:00</div>
                <div class="card-description">Service runtime</div>
            </div>
        </div>

        <div class="status-card">
            <div class="card-header">
                <div class="card-icon">🧅</div>
                <div class="card-title">
                    <span class="status-indicator" id="torIndicator"></span>
                    TOR Network Status
                </div>
            </div>
            <div class="card-value" id="status">Ready</div>
            <div class="onion-address" id="onionAddress">Onion Address: Waiting...</div>
        </div>

        <div class="controls">
            <button class="btn-primary" id="startBtn" onclick="startServices()">
                🚀 Launch Anonymous Monerod
            </button>
            <button class="btn-secondary" onclick="logout()" style="margin-left: 20px;">
                🔐 Logout
            </button>
        </div>

        <div class="logs-container">
            <div class="log-panel">
                <div class="log-header">
                    <span class="status-indicator" id="torLogIndicator"></span>
                    TOR Network Logs
                </div>
                <div class="log-content" id="torLogs">
                    Waiting for TOR service to start...
                </div>
            </div>

            <div class="log-panel">
                <div class="log-header">
                    <span class="status-indicator" id="monerodLogIndicator"></span>
                    Monerod Daemon Logs
                </div>
                <div class="log-content" id="monerodLogs">
                    Waiting for monerod service to start...
                </div>
            </div>
        </div>
    </div>

    <script>
        let isStarting = false;

        function updateStatus() {
            fetch('/api/status')
                .then(response => {
                    if (response.status === 401) {
                        window.location.href = '/login';
                        return;
                    }
                    return response.json();
                })
                .then(data => {
                    if (!data) return;
                    
                    // Update status indicators
                    const torIndicator = document.getElementById('torIndicator');
                    const torLogIndicator = document.getElementById('torLogIndicator');
                    const monerodLogIndicator = document.getElementById('monerodLogIndicator');

                    if (data.tor_running) {
                        torIndicator.className = 'status-indicator status-running';
                        torLogIndicator.className = 'status-indicator status-running';
                    } else {
                        torIndicator.className = 'status-indicator status-stopped';
                        torLogIndicator.className = 'status-indicator status-stopped';
                    }

                    if (data.monerod_running) {
                        monerodLogIndicator.className = 'status-indicator status-running';
                    } else {
                        monerodLogIndicator.className = 'status-indicator status-stopped';
                    }

                    // Update values
                    document.getElementById('status').textContent = data.status;
                    document.getElementById('onionAddress').textContent = 'Onion Address: ' + data.onion_address;
                    document.getElementById('blockHeight').textContent = data.block_height.toLocaleString();
                    document.getElementById('syncStatus').textContent = data.sync_status;
                    document.getElementById('miningStatus').textContent = data.mining_status;
                    document.getElementById('hashRate').textContent = data.hash_rate;
                    document.getElementById('connections').textContent = data.connections;
                    document.getElementById('uptime').textContent = data.uptime;

                    // Update start button
                    const startBtn = document.getElementById('startBtn');
                    if (data.tor_running && data.monerod_running) {
                        startBtn.textContent = '✅ Services Running';
                        startBtn.disabled = true;
                        isStarting = false;
                    } else if (isStarting) {
                        startBtn.textContent = '⏳ Starting Services...';
                        startBtn.disabled = true;
                    } else {
                        startBtn.textContent = '🚀 Launch Anonymous Monerod';
                        startBtn.disabled = false;
                    }
                })
                .catch(error => console.error('Status update error:', error));
        }

        function updateLogs() {
            // Update TOR logs
            fetch('/api/logs/tor')
                .then(response => response.json())
                .then(data => {
                    const torLogs = document.getElementById('torLogs');
                    torLogs.innerHTML = data.logs.join('\\n');
                    torLogs.scrollTop = torLogs.scrollHeight;
                })
                .catch(error => console.error('TOR logs error:', error));

            // Update monerod logs
            fetch('/api/logs/monerod')
                .then(response => response.json())
                .then(data => {
                    const monerodLogs = document.getElementById('monerodLogs');
                    monerodLogs.innerHTML = data.logs.join('\\n');
                    monerodLogs.scrollTop = monerodLogs.scrollHeight;
                })
                .catch(error => console.error('Monerod logs error:', error));
        }

        function startServices() {
            if (isStarting) return;
            
            isStarting = true;
            const startBtn = document.getElementById('startBtn');
            startBtn.textContent = '⏳ Starting Services...';
            startBtn.disabled = true;

            fetch('/api/start', { method: 'POST' })
                .then(response => {
                    if (response.status === 401) {
                        window.location.href = '/login';
                        return;
                    }
                    return response.json();
                })
                .then(data => {
                    if (data) {
                        console.log('Start request sent:', data.message);
                    }
                })
                .catch(error => {
                    console.error('Start error:', error);
                    isStarting = false;
                    startBtn.textContent = '🚀 Launch Anonymous Monerod';
                    startBtn.disabled = false;
                });
        }

        function logout() {
            if (confirm('Are you sure you want to logout?')) {
                window.location.href = '/logout';
            }
        }

        // Initialize
        updateStatus();
        updateLogs();

        // Set up periodic updates
        setInterval(updateStatus, 2000);  // Update status every 2 seconds
        setInterval(updateLogs, 5000);    // Update logs every 5 seconds
    </script>
</body>
</html>'''
    
    with open('templates/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    # Start the web server
    web_server = make_server('127.0.0.1', WEB_PORT, app, threaded=True)
    server_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
    server_thread.start()
    
    print(f"Web interface started at http://127.0.0.1:{WEB_PORT} Tor port: 80")
    print("Once TOR is running, it will also be accessible via the .onion address")

# ========== Main Application ==========
if __name__ == "__main__":
    print("*"*60)
    print("🧅 MONEROD TOR ANONYMOUS LAUNCHER")
    print("*"*60)
    print("Created by @parthavain (https://x.com/parthavain)")
    print("*"*60)
    
    # Get secure password from user
    master_password = get_secure_password()
    
    # Initial logs
    log_message('monerod', 'Monerod TOR Anonymous Launcher initialized')
    log_message('tor', 'TOR service ready to start')
    
    print(f"\n🌐 Local Web Interface: http://127.0.0.1:{WEB_PORT}")
    print("🔒 Web access is password protected for security")
    print("🧅 TOR access will be available after service starts")
    print("="*60)
    
  
    start_web_server()
    
    try:
       
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\\nShutting down services...")
        
     
        if tor_process:
            tor_process.terminate()
        if monerod_process:
            monerod_process.terminate()
        if web_server:
            web_server.shutdown()
            print("Services stopped.")
         
        print("Services stopped.")
        
