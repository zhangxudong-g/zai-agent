# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability, please report it responsibly:

1. **DO NOT** create a public GitHub Issue for security vulnerabilities
2. Send a detailed report to: zhangxudong.sun@gmail.com
3. Include in your report:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Any suggested fixes (optional)

## Security Features

### Sandboxing

Zai Agent implements two-layer sandboxing:

1. **Path Sandbox** (`security.py`) - Validates file paths before tool execution
2. **Execution Sandbox** (`sandbox.py`) - Isolates shell command execution

### Allowed Tools

Only whitelisted tools are enabled by default:
- `read`, `glob`, `grep`, `file_tree`, `outline` - Read-only operations
- `shell` - Limited to safe commands (git, ls, find, etc.)
- `write`, `edit` - File modification (workspace-restricted)

### Shell Command Restrictions

- Output limited to 5000 characters
- Execution timeout: 30 seconds
- Only pre-approved commands allowed

## Best Practices

When using Zai Agent:

1. **Review suggestions** before approving tool calls
2. **Set boundaries** with `AGENT_WORKSPACE` environment variable
3. **Use sandbox mode** (`EXECUTION_SANDBOX=docker`) for untrusted code
4. **Monitor sessions** via JSONL logs in `./sessions/`

## Security Updates

Security patches will be released promptly. Subscribe to release notifications on GitHub.
