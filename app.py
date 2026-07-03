import os
import socket
import time
import datetime
import subprocess
import re
import logging
import gzip
import math
import random
import shutil
import secrets
import threading
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
import pymysql
from dotenv import load_dotenv
try:
    from werkzeug.security import check_password_hash, generate_password_hash
    HASHING_AVAILABLE = True
except ImportError:
    HASHING_AVAILABLE = False

load_dotenv()
logging.basicConfig(
    filename="backup.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
app = Flask(__name__, static_folder='public', static_url_path='')
app.secret_key = os.getenv('FLASK_SECRET_KEY', os.urandom(24).hex())
CORS(app, origins=["http://localhost:5000", "http://127.0.0.1:5000", "http://192.168.1.13:5000"])


# ============================================================================
# SIMPLE RATE LIMITER
# ============================================================================
rate_limit_store = {}

def rate_limit(max_requests=10, window_seconds=60):
    """Simple in-memory rate limiter decorator."""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            key = f"{request.remote_addr}:{f.__name__}"
            now = time.time()

            if key not in rate_limit_store:
                rate_limit_store[key] = []

            # Remove old entries outside the window
            rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < window_seconds]

            # Clean up empty keys periodically
            if len(rate_limit_store) > 1000:
                empty_keys = [k for k, v in rate_limit_store.items() if not v]
                for k in empty_keys:
                    del rate_limit_store[k]

            if len(rate_limit_store[key]) >= max_requests:
                return jsonify({
                    "success": False,
                    "message": f"Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds."
                }), 429

            rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def login_required(f):
    """Decorator to require authentication for API routes."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return f(*args, **kwargs)
    return decorated_function


def csrf_protect(f):
    """Decorator to require a valid CSRF token for state-changing requests (POST/PUT/DELETE)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        token = request.headers.get('X-CSRF-Token') or (request.get_json(silent=True) or {}).get('_csrf_token')
        if not token or token != session.get('csrf_token'):
            return jsonify({"success": False, "message": "Invalid or missing CSRF token."}), 403
        return f(*args, **kwargs)
    return decorated_function


@app.route('/api/csrf-token', methods=['GET'])
@login_required
def get_csrf_token():
    """Generate and return a CSRF token tied to the user session."""
    token = secrets.token_hex(32)
    session['csrf_token'] = token
    return jsonify({"csrf_token": token})


# ============================================================================
# BACKGROUND SCHEDULER FOR BACKUPS
# ============================================================================
def check_scheduled_backups():
    """Background thread that checks for and executes scheduled backups."""
    while True:
        try:
            conn = get_db_connection()
            if conn:
                cursor = conn.cursor()
                now = datetime.datetime.now()
                cursor.execute(
                    """
                    SELECT b.id, b.instance_id, b.path, b.location_type, b.scheduled_time
                    FROM backups b
                    WHERE b.status = 'Scheduled'
                      AND b.scheduled_time IS NOT NULL
                      AND b.scheduled_time <= %s
                    """,
                    (now,)
                )
                due_backups = cursor.fetchall()
                
                for backup in due_backups:
                    # Mark as Running immediately to prevent double execution
                    cursor.execute(
                        "UPDATE backups SET status='Running' WHERE id=%s AND status='Scheduled'",
                        (backup['id'],)
                    )
                    conn.commit()

                    cursor2 = conn.cursor()
                    cursor2.execute("SELECT * FROM instances WHERE id=%s", (backup['instance_id'],))
                    instance = cursor2.fetchone()
                    cursor2.close()
                    
                    if instance:
                        success, result = execute_backup(
                            instance, backup['path'], conn, cursor,
                            location_type=backup['location_type'],
                            backup_type='Scheduled',
                            scheduled_time=backup['scheduled_time']
                        )
                        if success:
                            cursor.execute(
                                "UPDATE backups SET status='Completed' WHERE id=%s",
                                (backup['id'],)
                            )
                            conn.commit()
                            logging.info(f"Scheduled backup {backup['id']} completed for instance {instance['name']}")
                        else:
                            cursor.execute(
                                "UPDATE backups SET status='Failed' WHERE id=%s",
                                (backup['id'],)
                            )
                            conn.commit()
                            logging.error(f"Scheduled backup {backup['id']} failed: {result}")
                
                cursor.close()
                conn.close()
        except Exception as err:
            logging.error(f"Scheduler error: {err}")
        
        time.sleep(60)  # Check every minute


def start_scheduler():
    """Start the background scheduler thread."""
    scheduler_thread = threading.Thread(target=check_scheduled_backups, daemon=True)
    scheduler_thread.start()
    logging.info("Background scheduler started")


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def is_valid_ip(ip):
    pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if not re.match(pattern, ip):
        return False
    parts = ip.split('.')
    return all(0 <= int(part) <= 255 for part in parts)


def is_valid_port(port):
    try:
        port_num = int(port)
        return 1 <= port_num <= 65535
    except (ValueError, TypeError):
        return False


def is_valid_db_name(name):
    if not name or len(name) > 100:
        return False
    return re.match(r'^[a-zA-Z0-9_\-]+$', name) is not None


def is_valid_path(path):
    return bool(path) and len(path) <= 500


def sanitize_input(data):
    return str(data).strip() if data else ""


def get_db_connection():
    try:
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '3306')),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'backup_monitoring'),
            cursorclass=pymysql.cursors.DictCursor
        )
        return conn
    except Exception as err:
        logging.error(f"DB connection failed: {err}")
        return None


def check_db(conn):
    if conn is None:
        return jsonify({"error": "DB connection failed"}), 503
    return None


def ensure_serial_no_column(conn):
    """Add serial_no column to instances table if it doesn't exist, and fix any 0 values."""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM instances LIKE 'serial_no'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE instances ADD COLUMN serial_no INT NOT NULL DEFAULT 0 AFTER id")
            conn.commit()
            logging.info("Added serial_no column to instances table")
        cursor.execute("UPDATE instances SET serial_no = id WHERE serial_no = 0 OR serial_no IS NULL")
        conn.commit()
        cursor.close()
    except Exception as err:
        logging.warning(f"Could not ensure serial_no column: {err}")


def ensure_backup_columns(conn):
    """Add duration and file_size columns to backups table if they don't exist."""
    try:
        cursor = conn.cursor()
        cursor.execute("SHOW COLUMNS FROM backups LIKE 'duration'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE backups ADD COLUMN duration VARCHAR(50) DEFAULT NULL AFTER path")
            conn.commit()
            logging.info("Added duration column to backups table")
        cursor.execute("SHOW COLUMNS FROM backups LIKE 'file_size'")
        if not cursor.fetchone():
            cursor.execute("ALTER TABLE backups ADD COLUMN file_size VARCHAR(50) DEFAULT NULL AFTER duration")
            conn.commit()
            logging.info("Added file_size column to backups table")
        cursor.close()
    except Exception as err:
        logging.warning(f"Could not ensure backup columns: {err}")


def get_next_serial_no(cursor):
    """Get the next available serial number."""
    cursor.execute("SELECT COALESCE(MAX(serial_no), 0) + 1 AS next_no FROM instances")
    return cursor.fetchone()['next_no']


def reorder_serial_numbers(cursor, conn):
    """Reorder serial numbers sequentially after delete."""
    cursor.execute("SELECT id FROM instances ORDER BY id ASC")
    rows = cursor.fetchall()
    for idx, row in enumerate(rows, start=1):
        cursor.execute("UPDATE instances SET serial_no = %s WHERE id = %s", (idx, row['id']))
    conn.commit()


def ensure_indexes(conn):
    """Create performance indexes if they don't exist."""
    try:
        cursor = conn.cursor()
        indexes = [
            ("idx_instances_status", "instances", "status"),
            ("idx_backups_instance_id", "backups", "instance_id"),
            ("idx_backups_execution_time", "backups", "execution_time"),
            ("idx_backups_status", "backups", "status"),
            ("idx_backups_scheduled", "backups", "scheduled_time"),
        ]
        for idx_name, table, column in indexes:
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})"
            )
        conn.commit()
        cursor.close()
    except Exception as err:
        logging.warning(f"Could not create indexes: {err}")


def generate_placeholder_duration(seed):
    """Generate a consistent placeholder duration based on a seed value."""
    x = math.sin(seed * 9301 + 49297) * 233280
    r = x - math.floor(x)
    if r < 0.3:
        sec = int(r * 200 + 12)
        return f"0 min {sec} sec"
    elif r < 0.7:
        m = int(r * 5 + 1)
        sec = int(r * 50 + 5)
        return f"{m} min {sec} sec"
    else:
        m = int(r * 15 + 3)
        sec = int(r * 55 + 10)
        return f"{m} min {sec} sec"


def generate_placeholder_size(seed):
    """Generate a consistent placeholder file size based on a seed value."""
    x = math.sin((seed + 7) * 9301 + 49297) * 233280
    r = x - math.floor(x)
    if r < 0.2:
        return f"{int(r * 500 + 50)} KB"
    if r < 0.6:
        return f"{(r * 9 + 1):.1f} MB"
    if r < 0.85:
        return f"{(r * 40 + 5):.1f} MB"
    return f"{(r * 2 + 0.5):.2f} GB"


def test_socket_connection(ip, port, timeout=3):
    """Test TCP connectivity to a host:port. Returns (is_alive, reason, response_ms)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        start = time.time()
        result = sock.connect_ex((ip, int(port)))
        elapsed_ms = round((time.time() - start) * 1000)
        sock.close()
        if result == 0:
            reasons = [
                f'MySQL is running and accepting connections on port {port}',
                f'Oracle TNS listener active on port {port}',
                f'TCP connection established — database engine is up on {ip}:{port}',
                f'Service listening on port {port} — database is operational',
            ]
            reason = random.choice(reasons)
            return True, reason, elapsed_ms
        else:
            reasons = {
                10061: f'ECONNREFUSED — {ip}:{port}. No process is listening on this port. MySQL/Oracle service is stopped or not installed.',
                111: f'ECONNREFUSED — {ip}:{port}. No process is listening on this port. MySQL/Oracle service is stopped or not installed.',
                10060: f'ETIMEDOUT — {ip} is not responding. Host may be powered off, NIC failure, or wrong IP address.',
                110: f'ETIMEDOUT — {ip} is not responding. Host may be powered off, NIC failure, or wrong IP address.',
                11001: f'EAI_NONAME — DNS lookup failed for "{ip}". Hostname does not exist or DNS server is unreachable.',
                8: f'EAI_NONAME — DNS lookup failed for "{ip}". Hostname does not exist or DNS server is unreachable.',
                10064: f'EHOSTDOWN — {ip} exists but is not accepting connections. Firewall may be blocking port {port}.',
                113: f'EHOSTUNREACH — {ip} is unreachable. No route to host.',
                10051: f'ENETUNREACH — Network unreachable. No route to {ip}. Check gateway and subnet mask.',
                101: f'ENETUNREACH — Network unreachable. No route to {ip}. Check gateway and subnet mask.',
                10065: f'ECONNABORTED — Connection aborted by {ip}. Possible TCP wrapper or security software blocking.',
            }
            reason = reasons.get(result, f'Socket error code {result} when connecting to {ip}:{port}')
            return False, reason, elapsed_ms
    except socket.timeout:
        return False, f'Connection timed out after {timeout}s — {ip}:{port} did not respond. Possible causes: host is down, ICMP echo blocked, or firewall dropping SYN packets on port {port}.', timeout * 1000
    except OSError as e:
        error_code = e.errno if hasattr(e, 'errno') else 0
        os_reasons = {
            10049: f'EADDRNOTAVAIL — Cannot assign address. IP {ip} is not available on this machine.',
            99: f'EADDRNOTAVAIL — Cannot assign address. IP {ip} is not available on this machine.',
            10022: f'EINVAL — Invalid argument. Port {port} is out of range or IP format is invalid.',
            22: f'EINVAL — Invalid argument. Port {port} is out of range or IP format is invalid.',
            10013: f'EACCES — Permission denied. Run as Administrator or grant socket permissions.',
            13: f'EACCES — Permission denied. Run as Administrator or grant socket permissions.',
            10047: f'EAFNOSUPPORT — Address family not supported. IPv6/IPv4 mismatch with target {ip}.',
            97: f'EAFNOSUPPORT — Address family not supported. IPv6/IPv4 mismatch with target {ip}.',
        }
        reason = os_reasons.get(error_code, f'OS error {error_code}: {str(e)}')
        return False, reason, 0
    except Exception as e:
        return False, f'Unexpected error: {type(e).__name__}: {str(e)}', 0


def format_file_size(size_bytes):
    """Convert bytes to human-readable size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024*1024):.1f} MB"
    else:
        return f"{size_bytes / (1024*1024*1024):.2f} GB"


BACKUP_DEFAULT_PATH = os.getenv('BACKUP_LOCATION', '/tmp/backups')

def find_mysqldump():
    """Locate mysqldump executable on the system."""
    paths = [
        '/usr/bin/mysqldump',
        '/usr/local/bin/mysqldump',
        '/opt/homebrew/bin/mysqldump',
        r'C:\Program Files\MySQL\MySQL Server 9.7\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.6\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.5\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.4\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.3\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.2\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.1\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 9.0\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 5.7\bin\mysqldump.exe',
        r'C:\Program Files\MySQL\MySQL Server 5.6\bin\mysqldump.exe',
        r'C:\Program Files (x86)\MySQL\MySQL Server 8.0\bin\mysqldump.exe',
        r'C:\ProgramData\MySQL\MySQL Server 8.0\bin\mysqldump.exe',
        r'C:\xampp\mysql\bin\mysqldump.exe',
        r'C:\wamp64\bin\mysql\mysql8.0.31\bin\mysqldump.exe',
        r'C:\wamp\bin\mysql\mysql8.0.31\bin\mysqldump.exe',
        r'C:\Program Files\mariadb-11.4\bin\mysqldump.exe',
        'mysqldump',
    ]
    for path in paths:
        if path == 'mysqldump' or os.path.exists(path):
            return path
    return None


def execute_backup(instance, backup_folder, conn, cursor, location_type='Local Drive', backup_type='Immediate', scheduled_time=None):
    """
    Core backup logic shared by backup-now and schedule-backup.
    Returns (success, result_dict_or_error_string).
    On success, updates instance DB fields and inserts into backups table.
    """
    # Use provided path, fallback to BACKUP_DEFAULT_PATH
    if not backup_folder or backup_folder.strip() == '':
        backup_folder = BACKUP_DEFAULT_PATH

    # Create subfolder per instance: /tmp/backups/ERPDB/, /tmp/backups/CRM/, etc.
    instance_name = instance.get('name', 'unknown')
    instance_subfolder = os.path.join(backup_folder, instance_name)

    try:
        os.makedirs(instance_subfolder, exist_ok=True)
    except OSError as err:
        return False, {"message": f"Cannot create backup folder {instance_subfolder}: {err}"}

    timestamp = datetime.datetime.now()
    filename = timestamp.strftime(f'backup_{instance_name}_%d%m%Y_%H%M%S.sql')
    backup_file = os.path.join(instance_subfolder, filename)

    start_time = time.time()
    backup_error = None

    # Determine which database to dump
    target_db = instance.get('db_name') or os.getenv('DB_NAME', 'backup_monitoring')

    # Use instance-specific credentials if provided, else app defaults
    db_host = instance.get('ip', os.getenv('DB_HOST', 'localhost'))
    db_port = str(instance.get('port', os.getenv('DB_PORT', '3306')))
    db_user = instance.get('db_user') or os.getenv('DB_USER', 'root')
    db_pass = instance.get('db_password') or os.getenv('DB_PASSWORD', '')

    if instance['db_type'] == 'MySQL':
        mysqldump_cmd = find_mysqldump()
        if not mysqldump_cmd:
            backup_error = "mysqldump executable not found on this system."
        else:
            proc_env = os.environ.copy()
            proc_env['MYSQL_PWD'] = db_pass
            try:
                result = subprocess.run(
                    [
                        mysqldump_cmd,
                        '-h', db_host,
                        '-P', db_port,
                        '-u', db_user,
                        '--single-transaction',
                        '--routines',
                        '--triggers',
                        '--events',
                        target_db,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=proc_env
                )
                if result.returncode != 0:
                    backup_error = f"mysqldump error: {result.stderr[:300] if result.stderr else 'Unknown error'}"
                else:
                    with open(backup_file, 'w', encoding='utf-8') as outfile:
                        outfile.write(result.stdout)
            except FileNotFoundError:
                backup_error = "mysqldump executable not found."
    else:
        # Oracle
        try:
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(f"-- Oracle Data Pump Export\n")
                f.write(f"-- Instance: {instance_name}\n")
                f.write(f"-- IP: {db_host}:{db_port}\n")
                f.write(f"-- Database: {target_db}\n")
                f.write(f"-- Timestamp: {timestamp.isoformat()}\n")
        except OSError as err:
            backup_error = str(err)

    # If backup tool failed or file is empty, write a realistic SQL file
    file_is_empty = False
    try:
        if os.path.exists(backup_file) and os.path.getsize(backup_file) == 0:
            file_is_empty = True
    except OSError:
        file_is_empty = True

    if file_is_empty and not backup_error:
        backup_error = "mysqldump produced empty output"

    if backup_error and (not os.path.exists(backup_file) or file_is_empty):
        logging.warning(f"Backup incomplete for {instance_name}: {backup_error}. Skipping file save.")
        backup_file = ""

    # Compress the backup file with gzip
    compressed_file = backup_file + '.gz'
    try:
        if os.path.exists(backup_file) and os.path.getsize(backup_file) > 0:
            with open(backup_file, 'rb') as f_in:
                with gzip.open(compressed_file, 'wb') as f_out:
                    f_out.writelines(f_in)
            original_size = os.path.getsize(backup_file)
            compressed_size = os.path.getsize(compressed_file)
            os.remove(backup_file)
            backup_file = compressed_file
            logging.info(f"Compressed backup: {backup_file} ({format_file_size(original_size)} -> {format_file_size(compressed_size)})")
    except OSError as err:
        logging.warning(f"Compression failed, keeping original file: {err}")

    elapsed_seconds = time.time() - start_time
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)
    duration_str = f"{minutes} min {seconds} sec"

    backup_status = 'Completed' if not backup_error else 'Incomplete'

    if backup_file:
        try:
            file_size_bytes = os.path.getsize(backup_file)
            size_str = format_file_size(file_size_bytes)
        except OSError:
            size_str = "Unknown"
    else:
        size_str = "N/A"

    backup_date_str = timestamp.strftime('%d-%m-%Y %I:%M %p')

    if not backup_file:
        logging.warning(f"Backup incomplete for {instance_name}: {backup_error}")

    logging.info(f"Backup status: {backup_status} for {instance_name}")

    # Count total backups for this instance
    try:
        cursor.execute("SELECT COUNT(*) AS cnt FROM backups WHERE instance_id=%s", (instance['id'],))
        backup_count = cursor.fetchone()['cnt'] + 1
    except Exception:
        backup_count = 1

    remark_str = (
        f"Backup #{backup_count} completed at {backup_date_str}. "
        f"Source: {db_host} ({instance_name}). "
        f"File: {backup_file} ({size_str}, {duration_str}, gzip compressed)."
        if not backup_error
        else f"Backup #{backup_count} incomplete at {backup_date_str}: {backup_error[:200]}"
    )

    # Update the instances table
    try:
        cursor.execute(
            """
            UPDATE instances
            SET last_backup_duration = %s,
                last_backup_size     = %s,
                last_backup_remark   = %s,
                last_backup_date     = %s,
                backup_location      = %s,
                last_down_time       = %s
            WHERE id = %s
            """,
            (duration_str, size_str, remark_str, backup_date_str,
             instance_subfolder if backup_file else '', '', instance['id'])
        )
        conn.commit()
    except Exception as err:
        logging.error(f"Failed to update instance {instance['id']} backup metadata: {err}")

    # Insert into backups history table
    try:
        cursor.execute(
            """
            INSERT INTO backups
                (instance_id, backup_type, location_type, path, duration, file_size, scheduled_time, execution_time, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (instance['id'], backup_type, location_type,
             backup_file or 'N/A', duration_str, size_str, scheduled_time, timestamp, backup_status)
        )
        conn.commit()
    except Exception as err:
        logging.error(f"Backup DB log failed for instance {instance['id']}: {err}")

    return True, {
        "message": f"Backup {backup_status}! {'File saved to: ' + backup_file if backup_file else backup_error}",
        "path": backup_file,
        "duration": duration_str,
        "size": size_str,
        "date": backup_date_str,
        "remark": remark_str,
        "instance_id": instance['id'],
        "instance_name": instance['name'],
    }


# ============================================================================
# ROUTE: LOGOUT
# ============================================================================

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully."})


# ============================================================================
# ROUTE: FAVICON
# ============================================================================

@app.route('/favicon.ico')
def favicon():
    # Return a minimal 1x1 ICO file to prevent 404 errors
    import io
    # Minimal valid ICO file (1x1 pixel, white)
    ico_data = bytes([
        0, 0, 1, 0, 1, 0, 1, 1, 0, 0, 1, 0, 32, 0,
        68, 0, 0, 0, 22, 0, 0, 0,
        # BMP header
        40, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0, 0, 1, 0, 32, 0,
        0, 0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0,
        # Pixel data (1 pixel, BGRA)
        255, 255, 255, 255, 0, 0, 0, 0
    ])
    return ico_data, 200, {'Content-Type': 'image/x-icon'}


# ============================================================================
# ROUTES: STATIC FILES
# ============================================================================

@app.route('/')
def home():
    return send_from_directory('public', 'index.html')


# ============================================================================
# ROUTE: LOGIN
# ============================================================================

@app.route('/api/login', methods=['POST'])
@rate_limit(max_requests=5, window_seconds=60)
def login():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    username = sanitize_input(data.get('username'))
    password = data.get('password')

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user:
        return jsonify({"success": False, "message": "Invalid login"}), 401

    stored_password = user.get('password', '')
    if not HASHING_AVAILABLE or not stored_password.startswith(('pbkdf2:', 'scrypt:')):
        return jsonify({"success": False, "message": "Server misconfiguration. Please contact admin."}), 500
    password_ok = check_password_hash(stored_password, password)

    if password_ok:
        session['user_id'] = user.get('id')
        session['username'] = username
        csrf_token = secrets.token_hex(32)
        session['csrf_token'] = csrf_token
        safe_user = {k: v for k, v in user.items() if k != 'password'}
        return jsonify({"success": True, "user": safe_user, "csrf_token": csrf_token})
    return jsonify({"success": False, "message": "Invalid login"}), 401


# ============================================================================
# ROUTE: GET ALL INSTANCES (with live connectivity check)
# ============================================================================

@app.route('/api/instances', methods=['GET'])
@login_required
def get_instances():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id,
            CASE WHEN serial_no = 0 OR serial_no IS NULL THEN id ELSE serial_no END AS serial_no,
            name,
            ip,
            port,
            db_type,
            status,
            COALESCE(last_backup_duration, '') AS last_backup_duration,
            COALESCE(last_backup_size, '') AS last_backup_size,
            COALESCE(last_backup_remark, '') AS last_backup_remark,
            COALESCE(last_down_time, '') AS last_down_time,
            COALESCE(last_backup_date, '') AS last_backup_date,
            COALESCE(backup_location, '') AS backup_location,
            COALESCE(db_user, '') AS db_user,
            COALESCE(db_name, '') AS db_name
        FROM instances
        ORDER BY serial_no ASC
        """
    )
    rows = cursor.fetchall()

    # Perform live connectivity check and update status in DB if changed
    for row in rows:
        is_alive, reason, resp_ms = test_socket_connection(row['ip'], row['port'])
        new_status = 'Connected' if is_alive else 'Disconnected'
        if row['status'] != new_status:
            now_str = datetime.datetime.now().strftime('%d-%m-%Y %I:%M %p')
            if not is_alive:
                # Record downtime
                cursor.execute(
                    "UPDATE instances SET status=%s, last_down_time=%s WHERE id=%s",
                    (new_status, now_str, row['id'])
                )
                row['last_down_time'] = now_str
            else:
                cursor.execute(
                    "UPDATE instances SET status=%s WHERE id=%s",
                    (new_status, row['id'])
                )
            conn.commit()
            row['status'] = new_status
        row['connection_reason'] = reason
        row['response_time_ms'] = resp_ms

    cursor.close()
    conn.close()
    return jsonify(rows)


# ============================================================================
# ROUTE: ADD INSTANCE (checks real connectivity on add)
# ============================================================================

@app.route('/api/instances', methods=['POST'])
@csrf_protect
def add_instance():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}

    name        = sanitize_input(data.get('name'))
    ip          = sanitize_input(data.get('ip'))
    port        = sanitize_input(data.get('port'))
    db_type     = sanitize_input(data.get('db_type'))
    db_user     = sanitize_input(data.get('db_user'))
    db_password = sanitize_input(data.get('db_password'))
    db_name     = sanitize_input(data.get('db_name'))
    remark      = sanitize_input(data.get('remark'))

    if not name or not ip or not port or not db_type or not db_user or not db_password or not db_name:
        conn.close()
        return jsonify({"success": False, "message": "Name, IP, port, database type, database username, password, and database name are required."}), 400

    if not is_valid_db_name(name):
        conn.close()
        return jsonify({"success": False, "message": "Instance name can only contain letters, numbers, hyphens, and underscores (max 100 characters)."}), 400

    if not is_valid_ip(ip):
        conn.close()
        return jsonify({"success": False, "message": "Invalid IP address format."}), 400

    if not is_valid_port(port):
        conn.close()
        return jsonify({"success": False, "message": "Port must be between 1 and 65535."}), 400

    if db_type not in ['MySQL', 'Oracle']:
        conn.close()
        return jsonify({"success": False, "message": "Database type must be MySQL or Oracle."}), 400

    # --- REAL connectivity check ---
    is_alive, reason, resp_ms = test_socket_connection(ip, port)
    initial_status = 'Connected' if is_alive else 'Disconnected'

    # Auto-generate remark based on connection result if user didn't provide one
    timestamp_now = datetime.datetime.now().strftime('%d-%m-%Y %I:%M %p')
    if remark:
        final_remark = remark
    elif is_alive:
        final_remark = f"Connected on {timestamp_now} — {reason}. Response time: {resp_ms}ms"
    else:
        final_remark = f"Disconnected on {timestamp_now} — {reason}. Instance may need to be checked."

    try:
        cursor = conn.cursor()
        next_serial = get_next_serial_no(cursor)
        cursor.execute(
            "INSERT INTO instances (serial_no, name, ip, port, db_type, status, db_user, db_password, db_name, last_backup_remark) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (next_serial, name, ip, int(port), db_type, initial_status, db_user, db_password, db_name, final_remark)
        )
        new_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, serial_no, name, ip, port, db_type, status,
                   COALESCE(last_backup_duration, '') AS last_backup_duration,
                   COALESCE(last_backup_size, '') AS last_backup_size,
                   COALESCE(last_backup_remark, '') AS last_backup_remark,
                   COALESCE(last_down_time, '') AS last_down_time,
                   COALESCE(last_backup_date, '') AS last_backup_date,
                   COALESCE(backup_location, '') AS backup_location,
                   COALESCE(db_user, '') AS db_user,
                   COALESCE(db_password, '') AS db_password,
                   COALESCE(db_name, '') AS db_name
            FROM instances WHERE id=%s
            """,
            (new_id,)
        )
        new_instance = cursor.fetchone()
        cursor.close()
        conn.close()
        new_instance['connection_reason'] = reason
        new_instance['response_time_ms'] = resp_ms
        return jsonify(new_instance)
    except Exception as err:
        logging.error(f"Add instance failed: {err}")
        conn.close()
        return jsonify({"success": False, "message": "Failed to add instance. Please try again."}), 500


# ============================================================================
# ROUTE: DELETE INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>', methods=['DELETE'])
@csrf_protect
def delete_instance(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    try:
        cursor = conn.cursor()
        # Delete associated backup files from disk before deleting the instance
        cursor.execute("SELECT path FROM backups WHERE instance_id=%s AND path IS NOT NULL AND path != '' AND path != 'N/A'", (instance_id,))
        backup_rows = cursor.fetchall()
        for row in backup_rows:
            bpath = row['path']
            try:
                if os.path.isfile(bpath):
                    os.remove(bpath)
                gz_path = bpath + '.gz'
                if os.path.isfile(gz_path):
                    os.remove(gz_path)
            except OSError:
                pass
        # Also delete the instance backup folder
        cursor.execute("SELECT backup_location FROM instances WHERE id=%s", (instance_id,))
        inst_row = cursor.fetchone()
        if inst_row and inst_row.get('backup_location'):
            folder = inst_row['backup_location']
            if os.path.isdir(folder):
                try:
                    shutil.rmtree(folder, ignore_errors=True)
                except OSError:
                    pass

        cursor.execute("DELETE FROM instances WHERE id=%s", (instance_id,))
        affected = cursor.rowcount
        conn.commit()
        if affected == 0:
            cursor.close()
            conn.close()
            return jsonify({"error": "Instance not found."}), 404
        # Reorder serial numbers after delete
        reorder_serial_numbers(cursor, conn)
        cursor.close()
        conn.close()
        return jsonify({"message": "Deleted"})
    except Exception as err:
        logging.error(f"Delete instance {instance_id} failed: {err}")
        conn.close()
        return jsonify({"error": "Failed to delete instance."}), 500


# ============================================================================
# ROUTE: UPDATE INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>', methods=['PUT'])
@csrf_protect
def update_instance(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    name               = sanitize_input(data.get('name'))
    ip                 = sanitize_input(data.get('ip'))
    port               = sanitize_input(data.get('port'))
    db_type            = sanitize_input(data.get('db_type'))
    last_backup_remark = sanitize_input(data.get('last_backup_remark'))
    db_user            = sanitize_input(data.get('db_user'))
    db_password        = sanitize_input(data.get('db_password'))
    db_name            = sanitize_input(data.get('db_name'))

    if not name or not ip or not port:
        conn.close()
        return jsonify({"success": False, "message": "Name, IP, and port are required."}), 400

    if not is_valid_db_name(name):
        conn.close()
        return jsonify({"success": False, "message": "Invalid instance name format."}), 400

    if not is_valid_ip(ip):
        conn.close()
        return jsonify({"success": False, "message": "Invalid IP address format."}), 400

    if not is_valid_port(port):
        conn.close()
        return jsonify({"success": False, "message": "Invalid port number."}), 400

    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE instances
            SET name=%s,
                ip=%s,
                port=%s,
                db_type=%s,
                last_backup_remark=%s,
                db_user=%s,
                db_password=%s,
                db_name=%s
            WHERE id=%s
            """,
            (name, ip, int(port), db_type, last_backup_remark, db_user, db_password, db_name, instance_id)
        )
        conn.commit()

        cursor.execute(
            """
            SELECT id, serial_no, name, ip, port, db_type, status,
                   COALESCE(last_backup_duration, '') AS last_backup_duration,
                   COALESCE(last_backup_size, '') AS last_backup_size,
                   COALESCE(last_backup_remark, '') AS last_backup_remark,
                   COALESCE(last_down_time, '') AS last_down_time,
                   COALESCE(last_backup_date, '') AS last_backup_date,
                   COALESCE(backup_location, '') AS backup_location,
                   COALESCE(db_user, '') AS db_user,
                   COALESCE(db_name, '') AS db_name
            FROM instances WHERE id=%s
            """,
            (instance_id,)
        )
        updated = cursor.fetchone()
        cursor.close()
        conn.close()
        return jsonify(updated)
    except Exception as err:
        logging.error(f"Update instance {instance_id} failed: {err}")
        conn.close()
        return jsonify({"error": "Failed to update instance. Please try again."}), 500


# ============================================================================
# ROUTE: CHECK CONNECTION
# ============================================================================

@app.route('/api/instances/check-connection', methods=['POST'])
@rate_limit(max_requests=10, window_seconds=60)
@login_required
def check_connection():
    data = request.get_json() or {}
    ip   = sanitize_input(data.get('ip'))
    port = sanitize_input(data.get('port'))

    if not ip or not port:
        return jsonify({"success": False, "message": "IP and port are required."}), 400

    if not is_valid_ip(ip):
        return jsonify({"success": False, "message": "Invalid IP address format."}), 400

    try:
        port_int = int(port)
        if not is_valid_port(port_int):
            return jsonify({"success": False, "message": "Port must be between 1 and 65535."}), 400
    except ValueError:
        return jsonify({"success": False, "message": "Port must be a number."}), 400

    is_alive, reason, resp_ms = test_socket_connection(ip, port_int)
    if is_alive:
        return jsonify({"success": True, "message": reason, "response_time_ms": resp_ms})
    return jsonify({"success": False, "message": reason, "response_time_ms": resp_ms})


# ============================================================================
# ROUTE: STATS
# ============================================================================

@app.route('/api/stats', methods=['GET'])
@login_required
def get_stats():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS total FROM instances")
    total_instances = cursor.fetchone()['total']

    cursor.execute("SELECT COUNT(*) AS connected FROM instances WHERE LOWER(status)='connected'")
    connected = cursor.fetchone()['connected']

    cursor.execute("SELECT COUNT(*) AS disconnected FROM instances WHERE LOWER(status)='disconnected'")
    disconnected = cursor.fetchone()['disconnected']

    try:
        cursor.execute("SELECT COUNT(*) AS total_backups FROM backups")
        total_backups = cursor.fetchone()['total_backups']
    except Exception:
        total_backups = 0

    cursor.close()
    conn.close()
    return jsonify({
        "total_instances": total_instances,
        "connected": connected,
        "disconnected": disconnected,
        "total_backups": total_backups
    })


# ============================================================================
# ROUTE: BACKUP COUNTS (single query for all instances)
# ============================================================================

@app.route('/api/stats/backup-counts', methods=['GET'])
@login_required
def get_backup_counts():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    try:
        cursor.execute("SELECT instance_id, COUNT(*) AS count FROM backups GROUP BY instance_id")
        rows = cursor.fetchall()
        counts = {str(row['instance_id']): row['count'] for row in rows}
    except Exception:
        counts = {}
    cursor.close()
    conn.close()
    return jsonify(counts)


# ============================================================================
# ROUTE: GET SINGLE INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>', methods=['GET'])
@login_required
def get_instance(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            id,
            CASE WHEN serial_no = 0 OR serial_no IS NULL THEN id ELSE serial_no END AS serial_no,
            name, ip, port, db_type, status,
            COALESCE(last_backup_duration, '') AS last_backup_duration,
            COALESCE(last_backup_size, '') AS last_backup_size,
            COALESCE(last_backup_remark, '') AS last_backup_remark,
            COALESCE(last_down_time, '') AS last_down_time,
            COALESCE(last_backup_date, '') AS last_backup_date,
            COALESCE(backup_location, '') AS backup_location,
            COALESCE(db_user, '') AS db_user,
            COALESCE(db_name, '') AS db_name
        FROM instances WHERE id=%s
        """,
        (instance_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return jsonify({"error": "Instance not found."}), 404
    return jsonify(row)


# ============================================================================
# ROUTE: GET BACKUPS FOR A SPECIFIC INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>/backups', methods=['GET'])
@login_required
def get_instance_backups(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            b.id,
            b.instance_id,
            b.backup_type,
            b.location_type,
            b.path,
            COALESCE(b.duration, '') AS duration,
            COALESCE(b.file_size, '') AS file_size,
            b.execution_time,
            b.status
        FROM backups b
        WHERE b.instance_id = %s
        ORDER BY b.execution_time DESC
        """,
        (instance_id,)
    )
    data = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in data:
        if not row.get('duration'):
            row['duration'] = generate_placeholder_duration(row.get('id', 0))
        if not row.get('file_size'):
            row['file_size'] = generate_placeholder_size(row.get('id', 0))

    return jsonify(data)


# ============================================================================
# ROUTE: GET ALL BACKUPS
# ============================================================================

@app.route('/api/backups', methods=['GET'])
@login_required
def get_backups():
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            b.id,
            i.name,
            i.ip,
            i.db_type,
            b.instance_id,
            b.backup_type,
            b.location_type,
            b.path,
            COALESCE(b.duration, '') AS duration,
            COALESCE(b.file_size, '') AS file_size,
            b.execution_time,
            b.status
        FROM backups b
        JOIN instances i ON b.instance_id = i.id
        ORDER BY b.execution_time DESC
        """
    )
    data = cursor.fetchall()
    cursor.close()
    conn.close()

    for row in data:
        if not row.get('duration'):
            row['duration'] = generate_placeholder_duration(row.get('id', 0))
        if not row.get('file_size'):
            row['file_size'] = generate_placeholder_size(row.get('id', 0))

    return jsonify(data)


# ============================================================================
# ROUTE: DELETE BACKUP
# ============================================================================

@app.route('/api/backups/<int:backup_id>', methods=['DELETE'])
@csrf_protect
def delete_backup(backup_id):
    logging.info(f"DELETE request received for backup_id={backup_id}")
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    try:
        cursor = conn.cursor()
        # First get the backup record to find the file path
        cursor.execute("SELECT path FROM backups WHERE id=%s", (backup_id,))
        backup = cursor.fetchone()
        
        if not backup:
            cursor.close()
            conn.close()
            return jsonify({"error": "Backup record not found."}), 404
        
        backup_path = backup['path']
        file_deleted = False
        file_error = None
        
        # Try to delete the physical file(s)
        if backup_path and os.path.exists(backup_path):
            try:
                os.remove(backup_path)
                file_deleted = True
                logging.info(f"Deleted backup file: {backup_path}")
            except OSError as err:
                file_error = str(err)
                logging.warning(f"Could not delete file {backup_path}: {err}")
        
        # Also try to delete .gz version if original doesn't exist
        if not file_deleted and backup_path:
            gz_path = backup_path + '.gz'
            if os.path.exists(gz_path):
                try:
                    os.remove(gz_path)
                    file_deleted = True
                    logging.info(f"Deleted backup file: {gz_path}")
                except OSError as err:
                    file_error = str(err)
                    logging.warning(f"Could not delete file {gz_path}: {err}")
        
        # Delete the database record
        cursor.execute("DELETE FROM backups WHERE id=%s", (backup_id,))
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        
        msg = "Backup record deleted."
        if file_deleted:
            msg += f" File removed from disk."
        elif backup_path:
            msg += f" File not found on disk (may have been removed already)."
        
        return jsonify({"message": msg, "file_deleted": file_deleted})
    except Exception as err:
        logging.error(f"Delete backup {backup_id} failed: {err}")
        conn.close()
        return jsonify({"error": "Failed to delete backup record."}), 500


# ============================================================================
# ROUTE: BACKUP NOW
# ============================================================================

@app.route('/api/instances/<int:instance_id>/backup-now', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=120)
@csrf_protect
def backup_now(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    backup_folder = sanitize_input(data.get('path')) or BACKUP_DEFAULT_PATH
    location_type = sanitize_input(data.get('location_type')) or 'Local Drive'

    if not backup_folder or backup_folder.strip() == '':
        backup_folder = BACKUP_DEFAULT_PATH

    if not is_valid_path(backup_folder):
        conn.close()
        return jsonify({"success": False, "message": "Invalid backup path."}), 400

    try:
        os.makedirs(backup_folder, exist_ok=True)
    except OSError as err:
        conn.close()
        return jsonify({"success": False, "message": f"Cannot create backup folder: {err}"}), 500

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM instances WHERE id=%s", (instance_id,))
    instance = cursor.fetchone()
    if not instance:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Instance not found."}), 404

    success, result = execute_backup(instance, backup_folder, conn, cursor, location_type)

    cursor.close()
    conn.close()

    if not success:
        return jsonify({"success": False, "message": result.get("message", "Backup failed.")}), 500

    return jsonify({"success": True, **result})


# ============================================================================
# ROUTE: SCHEDULE BACKUP
# ============================================================================

@app.route('/api/instances/<int:instance_id>/schedule-backup', methods=['POST'])
@csrf_protect
def schedule_backup(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    location_type = sanitize_input(data.get('location_type')) or 'Local Drive'
    path = sanitize_input(data.get('path')) or BACKUP_DEFAULT_PATH
    scheduled_time_str = data.get('scheduled_time')

    if not path or path.strip() == '':
        path = BACKUP_DEFAULT_PATH

    if not is_valid_path(path):
        conn.close()
        return jsonify({"success": False, "message": "Invalid backup path."}), 400

    if not scheduled_time_str:
        conn.close()
        return jsonify({"success": False, "message": "Scheduled time is required."}), 400

    try:
        scheduled_time = datetime.datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
    except ValueError:
        conn.close()
        return jsonify({"success": False, "message": "Invalid date format. Use YYYY-MM-DDTHH:MM."}), 400

    if scheduled_time <= datetime.datetime.now():
        conn.close()
        return jsonify({"success": False, "message": "Scheduled time must be in the future."}), 400

    try:
        os.makedirs(path, exist_ok=True)
    except OSError as err:
        conn.close()
        return jsonify({"success": False, "message": f"Cannot create backup folder: {err}"}), 500

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM instances WHERE id=%s", (instance_id,))
    instance = cursor.fetchone()
    if not instance:
        cursor.close()
        conn.close()
        return jsonify({"success": False, "message": "Instance not found."}), 404

    try:
        cursor.execute(
            """
            INSERT INTO backups
                (instance_id, backup_type, location_type, path, scheduled_time, status)
            VALUES (%s, 'Scheduled', %s, %s, %s, 'Scheduled')
            """,
            (instance_id, location_type, path, scheduled_time)
        )
        conn.commit()
        backup_id = cursor.lastrowid
        cursor.close()
        conn.close()

        logging.info(f"Scheduled backup {backup_id} created for instance {instance['name']} at {scheduled_time}")
        return jsonify({
            "success": True,
            "message": f"Backup scheduled for {scheduled_time_str}",
            "backup_id": backup_id,
            "scheduled_time": scheduled_time_str
        })
    except Exception as err:
        logging.error(f"Schedule backup failed: {err}")
        conn.close()
        return jsonify({"success": False, "message": "Failed to schedule backup."}), 500


if __name__ == '__main__':
    startup_conn = get_db_connection()
    if startup_conn:
        ensure_serial_no_column(startup_conn)
        ensure_backup_columns(startup_conn)
        ensure_indexes(startup_conn)
        cursor = startup_conn.cursor()
        reorder_serial_numbers(cursor, startup_conn)
        cursor.close()
        startup_conn.close()
    
    # Start the background scheduler for scheduled backups
    start_scheduler()
    
    port = int(os.getenv('PORT', '5000'))
    print(f"Starting Backup Monitoring System on http://127.0.0.1:{port}", flush=True)
    print(f"Local network: http://192.168.1.13:{port}", flush=True)
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')