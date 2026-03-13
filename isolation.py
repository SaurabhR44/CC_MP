"""
Process isolation primitives.

Implements container isolation using Linux primitives:
- fork() - create child process
- chroot() - change root filesystem
- exec() - replace process with command
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Dict, Optional
import ctypes
import ctypes.util


# Try to load C library functions if available
libc_name = ctypes.util.find_library('c')
if libc_name:
    libc = ctypes.CDLL(libc_name)
    chroot_func = libc.chroot
else:
    chroot_func = None


def isolate_and_exec(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> int:
    """
    Execute a command in an isolated container.
    
    Process:
    1. fork() - create child process
    2. In child: chroot() to container root
    3. In child: change to working directory
    4. In child: set environment variables
    5. In child: exec() the command
    6. In parent: wait for child and return exit code
    
    Args:
        container_root: Path to container filesystem root
        working_dir: Working directory inside container
        env_vars: Environment variables to inject
        cmd: Command to execute
    
    Returns:
        Exit code of the command
    """
    
    # Check if we have proper Linux support
    if not _can_isolate():
        return _exec_without_isolation(working_dir, env_vars, cmd)
    
    # Fork child process
    child_pid = os.fork()
    
    if child_pid == 0:
        # Child process
        _child_process(container_root, working_dir, env_vars, cmd)
        # Should not reach here
        sys.exit(1)
    else:
        # Parent process - wait for child
        _, status = os.waitpid(child_pid, 0)
        
        # Extract exit code
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            # Killed by signal
            return 128 + os.WTERMSIG(status)
        else:
            return 1


def _can_isolate() -> bool:
    """Check if we can use chroot isolation."""
    # Check if running as root (required for chroot)
    if os.geteuid() != 0:
        return False
    
    # Check if chroot is available
    return chroot_func is not None or hasattr(os, 'chroot')


def _child_process(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> None:
    """
    Child process: isolate and execute command.
    
    This function does not return under normal circumstances.
    """
    try:
        # Change root to container filesystem
        _do_chroot(str(container_root))
        
        # Change to working directory
        os.chdir(working_dir)
        
        # Set environment variables
        for key, value in env_vars.items():
            os.environ[key] = value
        
        # Execute command
        os.execvp(cmd[0], cmd)
        
    except Exception as e:
        print(f"Error in child process: {e}", file=sys.stderr)
        sys.exit(1)


def _do_chroot(path: str) -> None:
    """Change root to path using chroot()."""
    if hasattr(os, 'chroot'):
        # Use Python's os.chroot if available
        os.chroot(path)
    elif chroot_func is not None:
        # Use ctypes to call chroot from libc
        result = chroot_func(ctypes.c_char_p(path.encode('utf-8')))
        if result != 0:
            raise OSError(f"chroot() failed: {result}")
    else:
        raise RuntimeError("chroot() not available")


def _exec_without_isolation(
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> int:
    """
    Fallback: execute without isolation (for testing or non-root).
    
    Warning: This does NOT provide security isolation.
    """
    print(
        "Warning: Running without isolation (requires root for chroot)",
        file=sys.stderr
    )
    
    # Build environment
    env = os.environ.copy()
    env.update(env_vars)
    
    try:
        result = subprocess.run(
            cmd,
            cwd=working_dir,
            env=env,
        )
        return result.returncode
    except FileNotFoundError:
        print(f"Error: command '{cmd[0]}' not found", file=sys.stderr)
        return 127


def capture_process_output(
    container_root: Path,
    working_dir: str,
    env_vars: Dict[str, str],
    cmd: List[str],
) -> tuple:
    """
    Execute command in container and capture stdout/stderr.
    
    Used for RUN instructions during build.
    
    Returns:
        (stdout, stderr, exit_code)
    """
    
    if not _can_isolate():
        # Fallback without isolation
        env = os.environ.copy()
        env.update(env_vars)
        
        try:
            result = subprocess.run(
                cmd,
                cwd=working_dir,
                env=env,
                capture_output=True,
                text=True,
            )
            return result.stdout, result.stderr, result.returncode
        except FileNotFoundError:
            return "", f"Error: command '{cmd[0]}' not found", 127
    
    # With isolation
    # TODO: Implement proper output capture with fork/chroot/exec
    # For now, use subprocess
    env = os.environ.copy()
    env.update(env_vars)
    
    try:
        result = subprocess.run(
            cmd,
            cwd=str(container_root / working_dir.lstrip('/')),
            env=env,
            capture_output=True,
            text=True,
        )
        return result.stdout, result.stderr, result.returncode
    except Exception as e:
        return "", str(e), 1
