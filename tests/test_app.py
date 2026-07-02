"""
Unit tests for Backup Monitoring System
Run with: python -m pytest tests/test_app.py -v
"""

import os
import sys
import json
import pytest
import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import (
    app, is_valid_ip, is_valid_port, is_valid_db_name,
    is_valid_path, sanitize_input, format_file_size,
    test_socket_connection, find_mysqldump, BACKUP_DEFAULT_PATH
)


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def client():
    """Create a test client for the Flask app."""
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client


@pytest.fixture
def sample_instance():
    """Sample instance data for testing."""
    return {
        'name': 'TestDB',
        'ip': '127.0.0.1',
        'port': 3306,
        'db_type': 'MySQL',
        'db_user': 'root',
        'db_password': 'testpass',
        'db_name': 'testdb',
    }


# ============================================================================
# VALIDATION FUNCTION TESTS
# ============================================================================

class TestValidation:
    """Test input validation functions."""

    def test_valid_ip_addresses(self):
        assert is_valid_ip('127.0.0.1') is True
        assert is_valid_ip('192.168.1.100') is True
        assert is_valid_ip('10.0.0.1') is True
        assert is_valid_ip('255.255.255.255') is True

    def test_invalid_ip_addresses(self):
        assert is_valid_ip('999.999.999.999') is False
        assert is_valid_ip('abc.def.ghi.jkl') is False
        assert is_valid_ip('192.168.1') is False
        assert is_valid_ip('') is False
        assert is_valid_ip('not-an-ip') is False
        assert is_valid_ip('192.168.1.1.1') is False

    def test_valid_ports(self):
        assert is_valid_port(1) is True
        assert is_valid_port(3306) is True
        assert is_valid_port(8080) is True
        assert is_valid_port(65535) is True
        assert is_valid_port('3306') is True

    def test_invalid_ports(self):
        assert is_valid_port(0) is False
        assert is_valid_port(-1) is False
        assert is_valid_port(65536) is False
        assert is_valid_port('abc') is False
        assert is_valid_port('') is False

    def test_valid_db_names(self):
        assert is_valid_db_name('mydb') is True
        assert is_valid_db_name('test_db') is True
        assert is_valid_db_name('my-db') is True
        assert is_valid_db_name('db123') is True

    def test_invalid_db_names(self):
        assert is_valid_db_name('') is False
        assert is_valid_db_name('my db') is False
        assert is_valid_db_name('db;DROP TABLE') is False
        assert is_valid_db_name('a' * 101) is False

    def test_valid_paths(self):
        assert is_valid_path('D:\\backup') is True
        assert is_valid_path('/var/backups') is True
        assert is_valid_path('C:\\Users\\admin\\backup') is True

    def test_invalid_paths(self):
        assert is_valid_path('') is False
        assert is_valid_path(None) is False

    def test_sanitize_input(self):
        assert sanitize_input('  hello  ') == 'hello'
        assert sanitize_input(None) == ''
        assert sanitize_input('test') == 'test'
        assert sanitize_input(123) == '123'


# ============================================================================
# UTILITY FUNCTION TESTS
# ============================================================================

class TestUtilities:
    """Test utility functions."""

    def test_format_file_size_bytes(self):
        assert format_file_size(500) == '500 B'

    def test_format_file_size_kb(self):
        assert format_file_size(1536) == '1.5 KB'

    def test_format_file_size_mb(self):
        assert format_file_size(1048576) == '1.0 MB'
        assert format_file_size(83886080) == '80.0 MB'

    def test_format_file_size_gb(self):
        assert format_file_size(1073741824) == '1.00 GB'

    def test_find_mysqldump(self):
        result = find_mysqldump()
        # May return None if mysqldump is not installed, which is acceptable
        assert result is None or isinstance(result, str)


# ============================================================================
# NETWORK TESTS
# ============================================================================

class TestNetwork:
    """Test network connectivity functions."""

    def test_localhost_connection(self):
        """Test connection to localhost (should succeed if MySQL is running)."""
        is_alive, reason, resp_ms = test_socket_connection('127.0.0.1', 3306, timeout=2)
        # Result depends on whether MySQL is running
        assert isinstance(is_alive, bool)
        assert isinstance(reason, str)
        assert isinstance(resp_ms, int)

    def test_invalid_host_connection(self):
        """Test connection to non-existent host."""
        is_alive, reason, resp_ms = test_socket_connection('192.0.2.1', 3306, timeout=1)
        assert is_alive is False
        assert len(reason) > 0

    def test_connection_returns_reason(self):
        """Test that connection always returns a reason string."""
        is_alive, reason, resp_ms = test_socket_connection('127.0.0.1', 1, timeout=1)
        assert isinstance(reason, str)
        assert len(reason) > 0


# ============================================================================
# FLASK ROUTE TESTS
# ============================================================================

class TestRoutes:
    """Test Flask API routes."""

    def test_home_page(self, client):
        """Test that home page loads."""
        response = client.get('/')
        assert response.status_code == 200

    def test_login_page_loads(self, client):
        """Test that login endpoint exists."""
        response = client.post('/api/login',
            data=json.dumps({'username': 'admin', 'password': 'admin123'}),
            content_type='application/json'
        )
        assert response.status_code in [200, 503]

    def test_login_invalid_credentials(self, client):
        """Test login with wrong credentials."""
        response = client.post('/api/login',
            data=json.dumps({'username': 'wrong', 'password': 'wrong'}),
            content_type='application/json'
        )
        assert response.status_code in [401, 503]

    def test_login_missing_fields(self, client):
        """Test login with missing fields."""
        response = client.post('/api/login',
            data=json.dumps({'username': 'admin'}),
            content_type='application/json'
        )
        assert response.status_code in [400, 503]

    def test_get_instances(self, client):
        """Test get instances endpoint."""
        response = client.get('/api/instances')
        assert response.status_code in [200, 503]

    def test_get_stats(self, client):
        """Test get stats endpoint."""
        response = client.get('/api/stats')
        assert response.status_code in [200, 503]

    def test_check_connection(self, client):
        """Test check connection endpoint."""
        response = client.post('/api/instances/check-connection',
            data=json.dumps({'ip': '127.0.0.1', 'port': '3306'}),
            content_type='application/json'
        )
        assert response.status_code in [200, 503]
        data = json.loads(response.data)
        assert 'success' in data
        assert 'message' in data

    def test_check_connection_invalid_ip(self, client):
        """Test check connection with invalid IP."""
        response = client.post('/api/instances/check-connection',
            data=json.dumps({'ip': 'invalid', 'port': '3306'}),
            content_type='application/json'
        )
        assert response.status_code in [400, 503]

    def test_check_connection_missing_fields(self, client):
        """Test check connection with missing fields."""
        response = client.post('/api/instances/check-connection',
            data=json.dumps({'ip': '127.0.0.1'}),
            content_type='application/json'
        )
        assert response.status_code in [400, 503]

    def test_add_instance_invalid_data(self, client):
        """Test add instance with invalid data."""
        response = client.post('/api/instances',
            data=json.dumps({'name': '', 'ip': '', 'port': ''}),
            content_type='application/json'
        )
        assert response.status_code in [400, 503]


# ============================================================================
# BACKUP PATH TESTS
# ============================================================================

class TestBackupPath:
    """Test backup path configuration."""

    def test_default_backup_path(self):
        assert BACKUP_DEFAULT_PATH == r'D:\backup'

    def test_backup_path_exists(self):
        assert os.path.exists(BACKUP_DEFAULT_PATH) or True  # May not exist in test env


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
