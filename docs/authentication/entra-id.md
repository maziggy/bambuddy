# Azure Entra ID (Azure AD) OIDC Setup

This guide shows how to configure BamBuddy's OIDC integration with **Microsoft Azure Entra ID** (formerly Azure Active Directory).

---

## Prerequisites

- An Azure account with permission to register applications in Entra ID.
- BamBuddy ≥ 1.0 with OIDC enabled (Settings → Authentication → OIDC Providers).

---

## 1. Register an Application in Azure

1. Open the [Azure Portal](https://portal.azure.com) and navigate to **Entra ID → App registrations → New registration**.
2. Set a display name (e.g. `BamBuddy`).
3. Under **Supported account types**, select the option that matches your organisation.
4. Add a **Redirect URI** of type **Web**:
   ```
   https://<your-bambuddy-host>/api/v1/auth/oidc/callback
   ```
5. Click **Register**.

---

## 2. Create a Client Secret

1. In your app registration, go to **Certificates & secrets → Client secrets → New client secret**.
2. Choose an expiry and click **Add**.
3. **Copy the secret value immediately** — it is only shown once.

---

## 3. Gather the Required Values

| Value | Where to find it |
|---|---|
| **Issuer URL** | **Overview → Endpoints** — copy the *OpenID Connect metadata document* URL and strip `/.well-known/openid-configuration` from the end. It looks like `https://login.microsoftonline.com/<tenant-id>/v2.0`. |
| **Client ID** | **Overview → Application (client) ID** |
| **Client Secret** | The secret value you copied above |

---

## 4. Add the Provider in BamBuddy

Go to **Settings → Authentication → OIDC Providers → Add Provider** and fill in:

| Field | Value |
|---|---|
| Name | `Azure Entra ID` (or any label you prefer) |
| Issuer URL | `https://login.microsoftonline.com/<tenant-id>/v2.0` |
| Client ID | Your Application (client) ID |
| Client Secret | The secret you created |
| Scopes | `openid email profile` |
| **Email Claim** | `preferred_username` ← **important for Entra ID** |
| Require Email Verified | **Off** ← Entra ID never sends `email_verified` |
| Auto-link existing accounts | Keep **Off** unless you fully trust the IdP and have verified all existing user emails |

### Why `preferred_username`?

Azure Entra ID does **not** include an `email_verified` claim in its ID tokens. Using the standard `email` claim with *Require Email Verified* enabled would block every login. Two safe alternatives exist:

- **`preferred_username`** (recommended) — Entra ID always populates this with the UPN (e.g. `user@contoso.com`). BamBuddy treats it as an email-shaped identifier and skips the `email_verified` check entirely.
- **`email`** with *Require Email Verified* disabled — works but accepts the claim unconditionally; only appropriate when the Entra ID tenant is fully under your control.

---

## 5. (Optional) Token Lifetime

Azure Entra ID issues access tokens that expire after 1 hour by default. BamBuddy exchanges the OIDC code for its own JWT at callback time, so the Entra token lifetime does not affect the BamBuddy session length. You can adjust BamBuddy's `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` env var independently.

---

## 6. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Login redirects to Entra but returns "OIDC login failed" | Redirect URI mismatch — check the URI registered in Azure exactly matches BamBuddy's callback URL, including scheme and trailing path. |
| User created but email is empty | The `preferred_username` claim was not populated by Entra. Try the `email` claim with *Require Email Verified* off. |
| "Invalid client" error from Azure | Client secret has expired or was copied incorrectly. Rotate the secret in Azure and update the provider in BamBuddy. |
| Login works but wrong user is linked | `auto_link_existing_accounts` should remain **Off** until all local user emails are verified to match the Entra UPNs. |
