# -*- coding: utf-8 -*-
"""
Extract Microsoft Graph API refresh tokens from Outlook accounts.
Uses pure requests to simulate OAuth2 authorization code flow (no browser needed).

Output format: email----password----refresh_token----client_id

Usage:
  python extract_graph_tokens.py outlook_accounts/accounts_20260413_043056.txt
  python extract_graph_tokens.py --email user@outlook.com --password pass123
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")

import requests

try:
    import config  # noqa: F401  # load project .env for OUTLOOK_UI_LOCALE
except Exception:
    pass

# Thunderbird client — public, supports personal accounts.
# 用 Graph Mail.Read 资源域：下游 common/mailbox.get_code_by_token 走 Graph REST
# (/me/mailFolders/.../messages) 取码，必须拿 graph.microsoft.com 资源的 refresh_token；
# 之前用 outlook.office.com(IMAP) 资源的 token 无法换 Graph token，取码必失败。
CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
REDIRECT_URI = "http://localhost"
SCOPE = "offline_access https://graph.microsoft.com/Mail.Read"
OUTPUT_DIR = "outlook_accounts"
MICROSOFT_UI_LOCALE = os.environ.get("OUTLOOK_UI_LOCALE", "en-US").strip() or "en-US"


class _MicrosoftFormParser(HTMLParser):
    """Parse Microsoft forms independently of attribute order, quoting, or UI text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.inputs = {}
        self._form = None

    def handle_starttag(self, tag, attrs):
        attributes = {str(key).lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            if self._form is not None:
                self.forms.append(self._form)
            self._form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "post").lower(),
                "inputs": {},
            }
        elif tag.lower() == "input":
            name = attributes.get("name", "")
            if not name:
                return
            value = attributes.get("value", "")
            self.inputs[name.lower()] = value
            if self._form is not None:
                self._form["inputs"][name] = value

    def handle_endtag(self, tag):
        if tag.lower() == "form" and self._form is not None:
            self.forms.append(self._form)
            self._form = None

    def close(self):
        super().close()
        if self._form is not None:
            self.forms.append(self._form)
            self._form = None


def _parse_microsoft_forms(text, base_url=""):
    parser = _MicrosoftFormParser()
    parser.feed(text or "")
    parser.close()
    forms = []
    for form in parser.forms:
        action = html.unescape(form["action"] or base_url)
        forms.append({
            **form,
            "action": urllib.parse.urljoin(base_url, action),
        })
    return forms, parser.inputs


def _redirect_url(response, location):
    return urllib.parse.urljoin(getattr(response, "url", ""), html.unescape(location or ""))


def get_graph_token(email, password, idx=0):
    """Get refresh_token via pure HTTP OAuth flow (no browser)."""
    tag = f"[#{idx}]"
    session = requests.Session()
    session.trust_env = True  # Use system proxy (Clash) — avoids rate-limiting on account.live.com
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept-Language": f"{MICROSOFT_UI_LOCALE},en;q=0.8",
    })

    try:
        # Step 1: GET authorize URL
        auth_url = (
            f"https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
            f"?client_id={CLIENT_ID}"
            f"&response_type=code"
            f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
            f"&scope={urllib.parse.quote(SCOPE)}"
            f"&response_mode=query"
            f"&mkt={urllib.parse.quote(MICROSOFT_UI_LOCALE)}"
            f"&ui_locales={urllib.parse.quote(MICROSOFT_UI_LOCALE)}"
        )
        print(f"  {tag} {email} — fetching auth page...")
        resp = session.get(auth_url, timeout=30, allow_redirects=True)

        # Extract form data from MS login page
        text = resp.text
        login_forms, page_inputs = _parse_microsoft_forms(text, resp.url)

        # Flow token (PPFT) — embedded in sFTTag as escaped HTML input
        flow_token = ""
        sft_tag = re.search(r'sFTTag.*?value=\\?"([^"\\]+)', text)
        if sft_tag:
            flow_token = sft_tag.group(1)
        if not flow_token:
            flow_token = page_inputs.get("ppft", "")

        # Post URL
        post_url = ""
        urlpost_match = re.search(r'"urlPost"\s*:\s*"([^"]+)"', text)
        if urlpost_match:
            post_url = urlpost_match.group(1).replace("\\u0026", "&")

        # Context
        ctx = ""
        sctx_match = re.search(r'"sCtx"\s*:\s*"([^"]+)"', text)
        if sctx_match:
            ctx = sctx_match.group(1)

        if not flow_token:
            print(f"  {tag} FAIL: no flow token found")
            return None

        if not post_url:
            credential_form = next((
                form for form in login_forms
                if "ppft" in {name.lower() for name in form["inputs"]}
            ), None)
            post_url = (credential_form or {}).get("action") or (
                "https://login.live.com/ppsecure/post.srf"
            )
        else:
            post_url = urllib.parse.urljoin(resp.url, html.unescape(post_url))

        print(f"  {tag} submitting credentials...")

        # Step 2: POST credentials
        login_data = {
            "login": email,
            "loginfmt": email,
            "passwd": password,
            "PPFT": flow_token,
            "ctx": ctx,
            "type": "11",
            "LoginOptions": "3",
            "i13": "0",
            "CookieDisclosure": "0",
            "IsFidoSupported": "0",
            "isSignupPost": "0",
            "i19": "16393",
        }

        resp2 = session.post(post_url, data=login_data, timeout=30, allow_redirects=True)

        # Follow JS auto-submit intermediate pages (Microsoft uses onload="DoSubmit()" forms)
        for _ in range(5):
            _html = resp2.text or ''
            if ('dosubmit' in _html.lower() or ('fmhf' in _html.lower() and 'onload' in _html.lower())):
                auto_forms, _ = _parse_microsoft_forms(_html, resp2.url)
                if auto_forms:
                    auto_form = auto_forms[0]
                    resp2 = session.post(
                        auto_form["action"], data=auto_form["inputs"],
                        timeout=30, allow_redirects=True,
                    )
                    continue
            break

        # Follow redirects manually, catching localhost redirect
        auth_code = None
        for step in range(15):
            # Handle HTTP redirects
            while resp2.status_code in (301, 302, 303, 307):
                loc = _redirect_url(resp2, resp2.headers.get("Location", ""))
                if "localhost" in loc and "code=" in loc:
                    resp2 = type('R', (), {'url': loc, 'text': '', 'status_code': 200})()
                    break
                if "localhost" in loc and "error" in loc:
                    resp2 = type('R', (), {'url': loc, 'text': '', 'status_code': 200})()
                    break
                resp2 = session.get(loc, timeout=30, allow_redirects=False)

            url = resp2.url
            text = resp2.text if hasattr(resp2, 'text') and resp2.text else ''

            # Check if we landed on localhost with code
            if "localhost" in url and "code=" in url:
                parsed = urllib.parse.urlparse(url)
                params = urllib.parse.parse_qs(parsed.query)
                auth_code = params.get("code", [None])[0]
                if auth_code:
                    print(f"  {tag} got auth code!")
                    break

            # Check for error
            if "localhost" in url and "error" in url:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                err = parsed.get("error_description", parsed.get("error", ["?"]))[0]
                print(f"  {tag} OAuth error: {err[:100]}")
                return None

            # Consent/Update — Microsoft app consent page (React SPA, no static form).
            # Accept by POSTing ucaction=Yes with fields extracted from ServerData JS config.
            if "consent/update" in url.lower():
                m_sd = re.search(r'ServerData\s*=\s*(\{.*?\});', text, re.DOTALL)
                if m_sd:
                    sd = json.loads(m_sd.group(1))
                    form_data_consent = {
                        'ucaction': 'Yes',
                        'client_id': sd.get('sClientId', ''),
                        'scope': sd.get('sRawInputScopes', ''),
                        'cscope': sd.get('sRawInputGrantedScopes', ''),
                        'canary': sd.get('sCanary', ''),
                    }
                    print(f"  {tag} accepting Consent/Update...")
                    resp2 = session.post(url, data=form_data_consent, timeout=30, allow_redirects=False)
                    continue
                print(f"  {tag} FAIL: Consent/Update with no ServerData")
                return None

            # proofs/Add — Microsoft asking to add security info.
            # Skip by setting action="Skip" and submitting the form (mirrors JS: jQuery("#action").val("Skip"))
            if "proofs/add" in url.lower():
                proof_forms, _ = _parse_microsoft_forms(text, url)
                if proof_forms:
                    proof_form = proof_forms[0]
                    form_action2 = proof_form["action"]
                    form_data2 = dict(proof_form["inputs"])
                    form_data2["action"] = "Skip"  # simulate Skip button click
                    print(f"  {tag} skipping proofs/Add (action=Skip) -> {form_action2[:80]}...")
                    resp2 = session.post(form_action2, data=form_data2, timeout=30, allow_redirects=False)
                    continue
                print(f"  {tag} FAIL: proofs/Add with no form")
                return None

            # Find and submit any form on the page (consent, redirect, etc.)
            forms, _ = _parse_microsoft_forms(text, url)
            if forms:
                form = forms[0]
                form_action = form["action"]
                form_data = dict(form["inputs"])

                # For consent pages, add accept
                if "consent" in form_action.lower() or "consent" in url.lower():
                    form_data["ucaccept"] = "Yes"
                    print(f"  {tag} submitting consent...")

                # Don't follow redirect to localhost (it will fail)
                resp2 = session.post(form_action, data=form_data, timeout=30, allow_redirects=False)
                # Follow redirects but catch localhost
                while resp2.status_code in (301, 302, 303, 307):
                    loc = _redirect_url(resp2, resp2.headers.get("Location", ""))
                    if "localhost" in loc:
                        resp2 = type('R', (), {'url': loc, 'text': '', 'status_code': 200})()
                        break
                    elif loc:
                        resp2 = session.get(loc, timeout=30, allow_redirects=False)
                    else:
                        break
                continue

            print(f"  {tag} FAIL: stuck at {url[:100]} (status={resp2.status_code})")
            return None

        if not auth_code:
            print(f"  {tag} FAIL: no auth code extracted")
            return None

        # Step 3: Exchange code for tokens
        print(f"  {tag} exchanging code for tokens...")
        token_resp = session.post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            data={
                "client_id": CLIENT_ID,
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
            },
            timeout=30,
        )
        token_data = token_resp.json()

        if "access_token" in token_data:
            rt = token_data.get("refresh_token", "")
            print(f"  {tag} OK! refresh_token={'yes' if rt else 'no'}")
            return {
                "email": email,
                "password": password,
                "refresh_token": rt,
                "client_id": CLIENT_ID,
            }
        else:
            err = token_data.get("error_description", token_data.get("error", "?"))
            print(f"  {tag} token error: {err[:150]}")
            return None

    except Exception as e:
        print(f"  {tag} error: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Extract Graph API tokens")
    parser.add_argument("accounts_file", nargs="?")
    parser.add_argument("--email", "-e", type=str)
    parser.add_argument("--password", "-p", type=str)
    parser.add_argument("--concurrency", "-c", type=int, default=5)
    args = parser.parse_args()

    accounts = []
    if args.email and args.password:
        accounts.append((args.email, args.password))
    elif args.accounts_file:
        with open(args.accounts_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split("----")
                    if len(parts) >= 2:
                        accounts.append((parts[0], parts[1]))
    else:
        # Auto-scan: load all unlocked accounts from unlock_results/, skip already extracted
        unlock_dir = "unlock_results"

        # Collect emails that already have tokens
        token_emails = set()
        if os.path.isdir(OUTPUT_DIR):
            for tf in sorted(os.listdir(OUTPUT_DIR)):
                if tf.startswith("graph_tokens_") and tf.endswith(".txt"):
                    with open(os.path.join(OUTPUT_DIR, tf), "r", encoding="utf-8") as tf_f:
                        for line in tf_f:
                            parts = line.strip().split("----")
                            if parts and parts[0]:
                                token_emails.add(parts[0].lower())

        # Collect all unlocked accounts, deduplicate by email
        seen_emails: set = set()
        if os.path.isdir(unlock_dir):
            for uf in sorted(os.listdir(unlock_dir)):
                if uf.startswith("unlocked_clean_") and uf.endswith(".txt"):
                    with open(os.path.join(unlock_dir, uf), "r", encoding="utf-8") as uf_f:
                        for line in uf_f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = line.split("----")
                            if len(parts) >= 2:
                                email_lc = parts[0].lower()
                                if email_lc not in seen_emails and email_lc not in token_emails:
                                    accounts.append((parts[0], parts[1]))
                                    seen_emails.add(email_lc)

        if token_emails:
            print(f"  Skipping {len(token_emails)} already-extracted accounts")
        print(f"  Auto-loaded {len(accounts)} new accounts from {unlock_dir}/")

    if not accounts:
        print("  No accounts to process.")
        return

    print("=" * 60)
    print(f"  Graph API Token Extraction (pure HTTP)")
    print(f"  accounts={len(accounts)}  concurrency={args.concurrency}")
    print(f"  client_id={CLIENT_ID}")
    print("=" * 60)

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(get_graph_token, e, p, i + 1): (e, p) for i, (e, p) in enumerate(accounts)}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {len(results)}/{len(accounts)} tokens extracted")
    print("=" * 60)

    if results:
        out_file = os.path.join(OUTPUT_DIR, f"graph_tokens_{ts}.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            for r in results:
                f.write(f"{r['email']}----{r['password']}----{r.get('refresh_token','')}----{CLIENT_ID}\n")
        print(f"  Saved to: {out_file}")

        for r in results:
            rt = r.get("refresh_token", "")
            print(f"  [OK] {r['email']}  rt={rt[:50]}...")

    print("=" * 60)


if __name__ == "__main__":
    main()
