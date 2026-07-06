-- ==========================================
-- Add Backup History - Fixed for Windows paths
-- Run this in MySQL Workbench
-- ==========================================

USE backup_monitoring;

-- Step 1: Check your instances first
SELECT id, name, serial_no FROM instances ORDER BY serial_no;

-- Step 2: Insert backups (use double backslashes for Windows paths)
-- Replace the instance_id numbers with your actual IDs from Step 1

INSERT INTO backups (instance_id, backup_type, location_type, path, execution_time, status) VALUES
(1, 'Immediate', 'Local Drive', 'D:\\backup\\ICARD\\backup_ICARD_03072026_083000.sql.gz', '2026-07-03 08:30:00', 'Completed'),
(1, 'Scheduled', 'Local Drive', 'D:\\backup\\ICARD\\backup_ICARD_02072026_220000.sql.gz', '2026-07-02 22:00:00', 'Completed'),
(2, 'Immediate', 'Local Drive', 'D:\\backup\\HRMS\\backup_HRMS_03072026_091500.sql.gz', '2026-07-03 09:15:00', 'Failed'),
(3, 'Scheduled', 'Google Drive', 'D:\\backup\\ERP\\backup_ERP_02072026_233000.sql.gz', '2026-07-02 23:30:00', 'Completed'),
(4, 'Immediate', 'Local Drive', 'D:\\backup\\Payroll\\backup_Payroll_03072026_074500.sql.gz', '2026-07-03 07:45:00', 'Failed'),
(6, 'Immediate', 'Local Drive', 'D:\\backup\\Library\\backup_Library_03072026_100000.sql.gz', '2026-07-03 10:00:00', 'Failed'),
(8, 'Scheduled', 'Local Drive', 'D:\\backup\\Finance\\backup_Finance_01072026_220000.sql.gz', '2026-07-01 22:00:00', 'Completed'),
(3, 'Immediate', 'Local Drive', 'D:\\backup\\ERP\\backup_ERP_03072026_112000.sql.gz', '2026-07-03 11:20:00', 'Completed');

-- Step 3: Verify
SELECT b.id, i.name, b.backup_type, b.status, b.path 
FROM backups b 
JOIN instances i ON b.instance_id = i.id 
ORDER BY b.execution_time DESC;
