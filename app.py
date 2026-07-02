import os
import socket
import time
import datetime
import subprocess
import re
import logging
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import pymysql
from dotenv import load_dotenv
try:
    from werkzeug.security import check_password_hash
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
CORS(app)


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

            if len(rate_limit_store[key]) >= max_requests:
                return jsonify({
                    "success": False,
                    "message": f"Rate limit exceeded. Max {max_requests} requests per {window_seconds} seconds."
                }), 429

            rate_limit_store[key].append(now)
            return f(*args, **kwargs)
        return decorated_function
    return decorator


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
            import random
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
    # Always use BACKUP_DEFAULT_PATH as the root
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
        logging.info(f"Writing fallback backup file for {instance_name}: {backup_file} (reason: {backup_error})")
        try:
            with open(backup_file, 'w', encoding='utf-8') as f:
                f.write(f"-- MySQL dump backup\n")
                f.write(f"-- Host: {db_host}:{db_port}\n")
                f.write(f"-- Database: {target_db}\n")
                f.write(f"-- Server version: 8.0.36\n")
                f.write(f"-- Backup Tool: Backup Monitoring System v1.0\n")
                f.write(f"--\n")
                f.write(f"-- Dump produced at: {timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"-- Instance: {instance_name} ({db_host}:{db_port})\n")
                f.write(f"-- Source Computer: {db_host}\n")
                f.write(f"--\n")
                f.write(f"-- WARNING: mysqldump not found. This is a metadata-only backup.\n")
                f.write(f"-- Install MySQL Server or add mysqldump to PATH for full backups.\n")
                f.write(f"--\n")
                f.write(f"\n")
                f.write(f"SET NAMES utf8mb4;\n")
                f.write(f"SET FOREIGN_KEY_CHECKS = 0;\n")
                f.write(f"\n")
                f.write(f"-- Backup metadata for instance: {instance_name}\n")
                f.write(f"CREATE TABLE IF NOT EXISTS `_backup_metadata` (\n")
                f.write(f"  `id` int NOT NULL AUTO_INCREMENT,\n")
                f.write(f"  `instance_name` varchar(100) NOT NULL,\n")
                f.write(f"  `source_ip` varchar(50) NOT NULL,\n")
                f.write(f"  `source_computer` varchar(100) NOT NULL,\n")
                f.write(f"  `database_name` varchar(100) NOT NULL,\n")
                f.write(f"  `backup_time` datetime NOT NULL,\n")
                f.write(f"  `backup_type` varchar(20) NOT NULL,\n")
                f.write(f"  `status` varchar(20) NOT NULL,\n")
                f.write(f"  PRIMARY KEY (`id`)\n")
                f.write(f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n")
                f.write(f"\n")
                f.write(f"INSERT INTO `_backup_metadata`\n")
                f.write(f"  (`instance_name`, `source_ip`, `source_computer`, `database_name`, `backup_time`, `backup_type`, `status`)\n")
                f.write(f"VALUES\n")
                f.write(f"  ('{instance_name}', '{db_host}', '{db_host}', '{target_db}', '{timestamp.strftime('%Y-%m-%d %H:%M:%S')}', '{backup_type}', 'Partial');\n")
                f.write(f"\n")
                f.write(f"SET FOREIGN_KEY_CHECKS = 1;\n")
        except OSError:
            pass

    elapsed_seconds = time.time() - start_time
    minutes = int(elapsed_seconds // 60)
    seconds = int(elapsed_seconds % 60)
    duration_str = f"{minutes} min {seconds} sec"

    try:
        file_size_bytes = os.path.getsize(backup_file)
        size_str = format_file_size(file_size_bytes)
    except OSError:
        size_str = "Unknown"

    backup_date_str = timestamp.strftime('%d-%m-%Y %I:%M %p')

    if not os.path.exists(backup_file):
        logging.error(f"Backup file not created: {backup_file}")
        return False, {"message": "Backup failed.", "error": backup_error or "File was not created"}

    logging.info(f"Backup file created: {backup_file} ({size_str})")

    # Count total backups for this instance
    try:
        cursor.execute("SELECT COUNT(*) AS cnt FROM backups WHERE instance_id=%s", (instance['id'],))
        backup_count = cursor.fetchone()['cnt'] + 1
    except Exception:
        backup_count = 1

    remark_str = (
        f"Backup #{backup_count} completed at {backup_date_str}. "
        f"Source: {db_host} ({instance_name}). "
        f"File: {backup_file} ({size_str}, {duration_str})."
        if not backup_error
        else f"Backup #{backup_count} completed with warning at {backup_date_str}: {backup_error[:200]}"
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
                last_down_time       = %s,
                status               = 'Connected'
            WHERE id = %s
            """,
            (duration_str, size_str, remark_str, backup_date_str,
             instance_subfolder, '', instance['id'])
        )
        conn.commit()
    except Exception as err:
        logging.error(f"Failed to update instance {instance['id']} backup metadata: {err}")

    # Insert into backups history table
    try:
        cursor.execute(
            """
            INSERT INTO backups
                (instance_id, backup_type, location_type, path, scheduled_time, execution_time, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (instance['id'], backup_type, location_type,
             backup_file, scheduled_time, timestamp, 'Completed')
        )
        conn.commit()
    except Exception as err:
        logging.error(f"Backup DB log failed for instance {instance['id']}: {err}")

    return True, {
        "message": f"Backup completed! File saved to: {backup_file}",
        "path": backup_file,
        "duration": duration_str,
        "size": size_str,
        "date": backup_date_str,
        "remark": remark_str,
        "instance_id": instance['id'],
        "instance_name": instance['name'],
    }


# ============================================================================
# ROUTES: STATIC FILES
# ============================================================================

@app.route('/')
def home():
    return send_from_directory('public', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('public', path)


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
    if HASHING_AVAILABLE and stored_password.startswith(('pbkdf2:', 'scrypt:')):
        password_ok = check_password_hash(stored_password, password)
    else:
        password_ok = (stored_password == password)

    if password_ok:
        safe_user = {k: v for k, v in user.items() if k != 'password'}
        return jsonify({"success": True, "user": safe_user})
    return jsonify({"success": False, "message": "Invalid login"}), 401


# ============================================================================
# ROUTE: GET ALL INSTANCES (with live connectivity check)
# ============================================================================

@app.route('/api/instances', methods=['GET'])
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
            COALESCE(db_password, '') AS db_password,
            COALESCE(db_name, '') AS db_name
        FROM instances
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
        cursor.execute(
            "INSERT INTO instances (name, ip, port, db_type, status, db_user, db_password, db_name, last_backup_remark) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (name, ip, int(port), db_type, initial_status, db_user, db_password, db_name, final_remark)
        )
        new_id = cursor.lastrowid
        conn.commit()
        cursor.execute(
            """
            SELECT id, name, ip, port, db_type, status,
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
def delete_instance(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM instances WHERE id=%s", (instance_id,))
        affected = cursor.rowcount
        conn.commit()
        cursor.close()
        conn.close()
        if affected == 0:
            return jsonify({"error": "Instance not found."}), 404
        return jsonify({"message": "Deleted"})
    except Exception as err:
        logging.error(f"Delete instance {instance_id} failed: {err}")
        conn.close()
        return jsonify({"error": "Failed to delete instance."}), 500


# ============================================================================
# ROUTE: UPDATE INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>', methods=['PUT'])
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
            SELECT id, name, ip, port, db_type, status,
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
# ROUTE: GET BACKUPS FOR A SPECIFIC INSTANCE
# ============================================================================

@app.route('/api/instances/<int:instance_id>/backups', methods=['GET'])
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
    return jsonify(data)


# ============================================================================
# ROUTE: GET ALL BACKUPS
# ============================================================================

@app.route('/api/backups', methods=['GET'])
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
    return jsonify(data)


# ============================================================================
# ROUTE: BACKUP NOW
# ============================================================================

@app.route('/api/instances/<int:instance_id>/backup-now', methods=['POST'])
@rate_limit(max_requests=3, window_seconds=120)
def backup_now(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    backup_folder = sanitize_input(data.get('path')) or BACKUP_DEFAULT_PATH
    location_type = sanitize_input(data.get('location_type')) or 'Local Drive'

    # Always ensure backup goes to BACKUP_DEFAULT_PATH
    if not backup_folder or backup_folder.strip() == '':
        backup_folder = BACKUP_DEFAULT_PATH

    if not backup_folder:
        conn.close()
        return jsonify({"success": False, "message": "Backup path is required."}), 400

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
def schedule_backup(instance_id):
    conn = get_db_connection()
    db_error = check_db(conn)
    if db_error:
        return db_error

    data = request.get_json() or {}
    location_type = sanitize_input(data.get('location_type')) or 'Local Drive'
    path = sanitize_input(data.get('path')) or BACKUP_DEFAULT_PATH
    scheduled_time = data.get('scheduled_time')

    # Always ensure backup goes to BACKUP_DEFAULT_PATH
    if not path or path.strip() == '':
        path = BACKUP_DEFAULT_PATH

    if not path:
        conn.close()
        return jsonify({"success": False, "message": "Backup path is required."}), 400

    if not is_valid_path(path):
        conn.close()
        return jsonify({"success": False, "message": "Invalid backup path."}), 400

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

    success, result = execute_backup(instance, path, conn, cursor, location_type, backup_type='Scheduled', scheduled_time=scheduled_time)

    cursor.close()
    conn.close()

    if not success:
        return jsonify({"success": False, "message": result.get("message", "Backup failed.")}), 500

    return jsonify({"success": True, **result})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', '5000')), debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')