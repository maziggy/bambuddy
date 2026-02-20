# Security Policy

## Reporting a Vulnerability

The Bambuddy team takes security seriously. We appreciate your efforts to responsibly disclose your findings.

### How to Report

**Please DO NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to:

**security@bambuddy.cool**

Or use GitHub's private vulnerability reporting feature:
1. Go to the [Security tab](https://github.com/maziggy/bambuddy/security)
2. Click "Report a vulnerability"
3. Fill out the form with details

### What to Include

Please include the following information in your report:

- **Description** of the vulnerability
- **Steps to reproduce** the issue
- **Affected versions** of Bambuddy
- **Potential impact** of the vulnerability
- **Any suggested fixes** (if you have them)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within 48 hours
- **Assessment**: We will investigate and validate the issue within 7 days
- **Updates**: We will keep you informed of our progress
- **Resolution**: We aim to release a fix within 30 days for critical issues
- **Credit**: We will credit you in our release notes (unless you prefer to remain anonymous)

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| 0.2.x   | :white_check_mark: |

## Security Considerations

### Network Security

Bambuddy communicates with your printers over your local network using:

- **MQTT over TLS** (port 8883) - Encrypted printer communication
- **FTPS** (port 990) - Encrypted file transfers

### Recommendations

1. **Run on trusted network**: Bambuddy should only be accessible on your local network
2. **Use reverse proxy**: If exposing to the internet, use a reverse proxy with HTTPS
3. **Keep updated**: Always run the latest version for security patches
4. **Secure API keys**: Treat API keys like passwords; don't share them publicly
5. **Developer Mode**: Use your printer's Developer Mode access code; don't share it

### Known Security Features

- API key authentication for external access
- No default credentials
- Local-only by default (no cloud dependency)
- TLS encryption for printer communication

## Scope

The following are **in scope** for security reports:

- Authentication/authorization bypasses
- Remote code execution
- SQL injection
- Cross-site scripting (XSS)
- Cross-site request forgery (CSRF)
- Sensitive data exposure
- Insecure direct object references

The following are **out of scope**:

- Issues in dependencies (report to the upstream project)
- Social engineering attacks
- Physical attacks
- Denial of service (DoS) attacks
- Issues requiring physical access to the server

## Acknowledgments

We thank the following individuals for responsibly disclosing security issues:

*No security issues have been reported yet.*

---

Thank you for helping keep Bambuddy and its users safe!
