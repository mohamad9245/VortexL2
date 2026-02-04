"""
VortexL2 Port Forward Management

Handles socat-based TCP port forwarding with systemd service management.
"""

import os
import subprocess
from pathlib import Path
from typing import List, Tuple, Dict, Optional


SYSTEMD_DIR = Path("/etc/systemd/system")
SERVICE_PREFIX = "vortexl2-forward@"
FORWARD_TEMPLATE = """[Unit]
Description=VortexL2 Port Forward - Port %i
After=network.target vortexl2-tunnel.service
Requires=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP4-LISTEN:%i,reuseaddr,fork TCP4:{remote_ip}:%i
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def run_command(cmd: str) -> Tuple[bool, str]:
    """Execute a shell command and return (success, output)."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout.strip() or result.stderr.strip()
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


class ForwardManager:
    """Manages socat port forwarding services."""
    
    def __init__(self, config):
        self.config = config
    
    def _get_service_name(self, port: int) -> str:
        """Get systemd service name for a port."""
        return f"{SERVICE_PREFIX}{port}.service"
    
    def _get_template_path(self) -> Path:
        """Get path to the template unit file."""
        return SYSTEMD_DIR / f"{SERVICE_PREFIX}.service"
    
    def install_template(self) -> Tuple[bool, str]:
        """Install the systemd template unit for port forwards."""
        remote_ip = self.config.remote_forward_ip
        if not remote_ip:
            return False, "Remote forward IP not configured"
        
        template_path = self._get_template_path()
        template_content = FORWARD_TEMPLATE.format(remote_ip=remote_ip)
        
        try:
            with open(template_path, 'w') as f:
                f.write(template_content)
            
            # Reload systemd
            run_command("systemctl daemon-reload")
            return True, f"Template installed at {template_path}"
        except Exception as e:
            return False, f"Failed to install template: {e}"
    
    def update_template(self, remote_ip: str = None) -> Tuple[bool, str]:
        """Update the template with new remote IP."""
        if remote_ip:
            self.config.remote_forward_ip = remote_ip
        return self.install_template()
    
    def create_forward(self, port: int) -> Tuple[bool, str]:
        """Create and start a port forward service."""
        # Ensure template exists
        if not self._get_template_path().exists():
            success, msg = self.install_template()
            if not success:
                return False, f"Failed to install template: {msg}"
        
        service_name = self._get_service_name(port)
        
        # Enable and start the service
        success, output = run_command(f"systemctl enable --now {service_name}")
        if not success:
            return False, f"Failed to create forward for port {port}: {output}"
        
        # Add to config
        self.config.add_port(port)
        
        return True, f"Port forward for {port} created and started"
    
    def remove_forward(self, port: int) -> Tuple[bool, str]:
        """Stop and disable a port forward service."""
        service_name = self._get_service_name(port)
        
        # Stop and disable
        run_command(f"systemctl stop {service_name}")
        run_command(f"systemctl disable {service_name}")
        
        # Remove from config
        self.config.remove_port(port)
        
        return True, f"Port forward for {port} removed"
    
    def add_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        """Add multiple port forwards from comma-separated string."""
        results = []
        ports = [p.strip() for p in ports_str.split(',') if p.strip()]
        
        for port_str in ports:
            try:
                port = int(port_str)
                if port < 1 or port > 65535:
                    results.append(f"Port {port}: Invalid port number")
                    continue
                
                success, msg = self.create_forward(port)
                results.append(f"Port {port}: {msg}")
            except ValueError:
                results.append(f"'{port_str}': Invalid port number")
        
        return True, "\n".join(results)
    
    def remove_multiple_forwards(self, ports_str: str) -> Tuple[bool, str]:
        """Remove multiple port forwards from comma-separated string."""
        results = []
        ports = [p.strip() for p in ports_str.split(',') if p.strip()]
        
        for port_str in ports:
            try:
                port = int(port_str)
                success, msg = self.remove_forward(port)
                results.append(f"Port {port}: {msg}")
            except ValueError:
                results.append(f"'{port_str}': Invalid port number")
        
        return True, "\n".join(results)
    
    def list_forwards(self) -> List[Dict]:
        """List all configured port forwards with their status."""
        forwards = []
        
        for port in self.config.forwarded_ports:
            service_name = self._get_service_name(port)
            
            # Check if service is active
            success, output = run_command(f"systemctl is-active {service_name}")
            status = output if success else "inactive"
            
            # Check if service is enabled
            success, output = run_command(f"systemctl is-enabled {service_name}")
            enabled = output if success else "disabled"
            
            forwards.append({
                "port": port,
                "service": service_name,
                "status": status,
                "enabled": enabled,
                "remote": f"{self.config.remote_forward_ip}:{port}"
            })
        
        return forwards
    
    def get_forward_status(self, port: int) -> Dict:
        """Get detailed status of a specific port forward."""
        service_name = self._get_service_name(port)
        
        success, output = run_command(f"systemctl status {service_name}")
        
        return {
            "port": port,
            "service": service_name,
            "detail": output
        }
    
    def restart_forward(self, port: int) -> Tuple[bool, str]:
        """Restart a specific port forward."""
        service_name = self._get_service_name(port)
        success, output = run_command(f"systemctl restart {service_name}")
        
        if not success:
            return False, f"Failed to restart port {port}: {output}"
        return True, f"Port forward for {port} restarted"
    
    def restart_all_forwards(self) -> Tuple[bool, str]:
        """Restart all configured port forwards."""
        results = []
        
        for port in self.config.forwarded_ports:
            success, msg = self.restart_forward(port)
            results.append(f"Port {port}: {msg}")
        
        if not results:
            return True, "No port forwards configured"
        
        return True, "\n".join(results)
    
    def stop_all_forwards(self) -> Tuple[bool, str]:
        """Stop all port forward services."""
        results = []
        
        for port in self.config.forwarded_ports:
            service_name = self._get_service_name(port)
            success, output = run_command(f"systemctl stop {service_name}")
            status = "stopped" if success else f"failed: {output}"
            results.append(f"Port {port}: {status}")
        
        if not results:
            return True, "No port forwards configured"
        
        return True, "\n".join(results)
    
    def start_all_forwards(self) -> Tuple[bool, str]:
        """Start all port forward services."""
        results = []
        
        for port in self.config.forwarded_ports:
            service_name = self._get_service_name(port)
            success, output = run_command(f"systemctl start {service_name}")
            status = "started" if success else f"failed: {output}"
            results.append(f"Port {port}: {status}")
        
        if not results:
            return True, "No port forwards configured"
        
        return True, "\n".join(results)
    
    def check_socat_installed(self) -> bool:
        """Check if socat is installed."""
        success, _ = run_command("which socat")
        return success
    
    def install_socat(self) -> Tuple[bool, str]:
        """Install socat package."""
        success, output = run_command("apt-get install -y socat")
        if not success:
            return False, f"Failed to install socat: {output}"
        return True, "socat installed successfully"
