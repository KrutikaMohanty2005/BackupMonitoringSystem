-- ==========================================
-- Database schema for Backup Monitoring System
-- Execute this script in MySQL Workbench
-- ==========================================

CREATE DATABASE IF NOT EXISTS backup_monitoring;
USE backup_monitoring;

-- 1. Users Table (for Authentication)
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL
);

-- Insert a default admin user
INSERT IGNORE INTO users (id, username, password) VALUES (1, 'admin', 'scrypt:32768:8:1$f4vIkf3mb2V3y7Xh$bae2cf829534a64ab592649f26825bad7ef02d775b6388ed623c3750bdc8cf7ba8679ac6dc885ac6a0b04a6a78346114414383e1a68ef3506a560c72f95a4cf6');

-- 2. Instances Table
CREATE TABLE IF NOT EXISTS instances (
    id INT AUTO_INCREMENT PRIMARY KEY,
    serial_no INT NOT NULL DEFAULT 0,
    name VARCHAR(100) NOT NULL,
    ip VARCHAR(50) NOT NULL,
    port INT NOT NULL,
    db_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) DEFAULT 'Disconnected',
    last_backup_duration VARCHAR(50) DEFAULT NULL,
    last_backup_size VARCHAR(50) DEFAULT NULL,
    last_backup_remark TEXT DEFAULT NULL,
    last_down_time VARCHAR(100) DEFAULT NULL,
    last_backup_date VARCHAR(100) DEFAULT NULL,
    backup_location VARCHAR(255) DEFAULT NULL,
    db_user VARCHAR(100) DEFAULT NULL,
    db_password VARCHAR(255) DEFAULT NULL,
    db_name VARCHAR(100) DEFAULT NULL
);

-- Instances are added via the web UI dashboard (Add New Instance tab)

-- 3. Backups history / schedule log table
CREATE TABLE IF NOT EXISTS backups (
    id INT AUTO_INCREMENT PRIMARY KEY,
    instance_id INT NOT NULL,
    backup_type VARCHAR(50) NOT NULL, -- 'Scheduled' or 'Immediate'
    location_type VARCHAR(100) NOT NULL, -- 'Local Drive', 'Google Drive', 'File Server'
    path VARCHAR(255) NOT NULL,
    duration VARCHAR(50) DEFAULT NULL,
    file_size VARCHAR(50) DEFAULT NULL,
    scheduled_time DATETIME DEFAULT NULL,
    execution_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) DEFAULT 'Completed',
    FOREIGN KEY (instance_id) REFERENCES instances(id) ON DELETE CASCADE
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_instances_status ON instances(status);
CREATE INDEX IF NOT EXISTS idx_backups_instance_id ON backups(instance_id);
CREATE INDEX IF NOT EXISTS idx_backups_execution_time ON backups(execution_time);
CREATE INDEX IF NOT EXISTS idx_backups_status ON backups(status);
CREATE INDEX IF NOT EXISTS idx_backups_scheduled ON backups(scheduled_time);
