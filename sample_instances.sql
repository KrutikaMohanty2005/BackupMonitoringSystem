-- ==========================================
-- Sample Instances for Backup Monitoring System
-- Run this in MySQL Workbench
-- ==========================================

USE backup_monitoring;

-- Add serial_no column if not exists
-- ALTER TABLE instances ADD COLUMN serial_no INT NOT NULL DEFAULT 0 AFTER id;

-- Connected instances (using 127.0.0.1 = localhost, likely reachable)
INSERT INTO instances (serial_no, name, ip, port, db_type, status, db_user, db_password, db_name, last_backup_remark) VALUES
(1,  'ICARD',       '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'icard_db',       'Instance "ICARD" connected — MySQL at localhost:3306'),
(2,  'HRMS',        '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'hrms_db',        'Instance "HRMS" connected — MySQL at localhost:3306'),
(3,  'ERP',         '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'erp_db',         'Instance "ERP" connected — MySQL at localhost:3306'),
(4,  'Payroll',     '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'payroll_db',     'Instance "Payroll" connected — MySQL at localhost:3306'),
(5,  'Finance',     '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'finance_db',     'Instance "Finance" connected — MySQL at localhost:3306'),
(6,  'Library',     '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'library_db',     'Instance "Library" connected — MySQL at localhost:3306'),
(7,  'StudentDB',   '127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'student_db',     'Instance "StudentDB" connected — MySQL at localhost:3306'),
(8,  'AttendanceDB','127.0.0.1', 3306, 'MySQL',    'Connected',    'root', 'admin123', 'attendance_db',  'Instance "AttendanceDB" connected — MySQL at localhost:3306');

-- Disconnected instances (using random IPs, likely unreachable)
INSERT INTO instances (serial_no, name, ip, port, db_type, status, db_user, db_password, db_name, last_backup_remark) VALUES
(9,  'CRM',         '10.99.99.99',   3306, 'MySQL',    'Disconnected', 'root', 'admin123', 'crm_db',         'Instance "CRM" disconnected — host unreachable'),
(10, 'Inventory',   '192.168.10.50', 1521, 'Oracle',   'Disconnected', 'system','oracle123','inventory_db',   'Instance "Inventory" disconnected — Oracle listener not responding'),
(11, 'Warehouse',   '10.200.50.25',  3306, 'MySQL',    'Disconnected', 'root', 'admin123', 'warehouse_db',   'Instance "Warehouse" disconnected — connection timed out'),
(12, 'HelpDesk',    '172.16.0.100',  1521, 'Oracle',   'Disconnected', 'system','oracle123','helpdesk_db',    'Instance "HelpDesk" disconnected — host powered off');

-- Backup history with varied statuses
INSERT INTO backups (instance_id, backup_type, location_type, path, duration, file_size, execution_time, status) VALUES
(1,  'Immediate',  'Local Drive',  'C:\backups\icard_01.sql',       '2 min 15 sec', '2.4 MB', '2026-07-03 08:30:00', 'Completed'),
(1,  'Scheduled',  'Local Drive',  'C:\backups\icard_02.sql',       '1 min 48 sec', '2.1 MB', '2026-07-02 22:00:00', 'Completed'),
(2,  'Immediate',  'Local Drive',  'C:\backups\hrms_01.sql',        '0 min 42 sec', '890 KB', '2026-07-03 09:15:00', 'Incomplete'),
(3,  'Scheduled',  'Google Drive', 'C:\backups\erp_01.sql',         '3 min 5 sec',  '4.2 MB', '2026-07-02 23:30:00', 'Completed'),
(4,  'Immediate',  'Local Drive',  'C:\backups\payroll_01.sql',     '0 min 8 sec',  '120 KB', '2026-07-03 07:45:00', 'Failed'),
(5,  'Scheduled',  'File Server',  '\\server\backups\finance.sql',  '4 min 20 sec', '6.8 MB', '2026-07-01 22:00:00', 'Completed'),
(6,  'Immediate',  'Local Drive',  'C:\backups\library_01.sql',     '1 min 30 sec', '1.7 MB', '2026-07-03 10:00:00', 'Completed'),
(7,  'Scheduled',  'Local Drive',  'C:\backups\student_01.sql',     '0 min 55 sec', '1.1 MB', '2026-07-02 21:00:00', 'Incomplete'),
(9,  'Immediate',  'Local Drive',  'C:\backups\crm_01.sql',         '2 min 50 sec', '3.3 MB', '2026-06-28 14:00:00', 'Completed'),
(10, 'Scheduled',  'Google Drive', 'C:\backups\inventory_01.sql',   '0 min 12 sec', '250 KB', '2026-06-25 22:00:00', 'Failed'),
(3,  'Immediate',  'Local Drive',  'C:\backups\erp_02.sql',         '3 min 10 sec', '4.5 MB', '2026-07-03 11:20:00', 'Completed'),
(5,  'Immediate',  'Local Drive',  'C:\backups\finance_02.sql',     '1 min 22 sec', '1.9 MB', '2026-07-03 06:00:00', 'Incomplete');
