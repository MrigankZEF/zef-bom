# Connecting an AI assistant to the ZEF BOM — read only

Ask questions about the live BOM from ChatGPT, Claude, Codex or any script, instead of
exporting a spreadsheet and uploading it. Everything here is **read only**. Nothing described
on this page can change a single number.

---

## 1. Get a token

A token is a long password for programs. It stands in for signing in with Google, which
assumes a human with a browser.

- If you are an admin: **Admin → API tokens → create one.**
- If you are not: ask an admin for one.

When you create it:

| Field | What to put |
|---|---|
| Label | The tool it is for — `chatgpt-rikkert`, `claude-laptop`, `weekly-report` |
| Account | Whose reading rights it borrows. A `viewer` account is the tightest choice |
| Expires | 30 days is plenty |

**Copy it immediately.** It is shown once, then never again — the server keeps only a
fingerprint of it.

**One token per tool, per person.** Then a mistake costs one revoke instead of a rebuild,
and the "Last used" column tells you which tokens are actually in use.

## 2. Keep it somewhere sensible

A token can read our part costs, suppliers, quotes and change history. Treat it like a
password.

- **Do** put it in a file on your own machine, or in your tool's password/secret field.
- **Do not** paste it into a chat, put it in the repository, or drop it in a shared doc.
  Anything you paste into a chat is stored by whoever runs that chat.

If it does end up somewhere it should not: **Admin → API tokens → Revoke.** It stops working
on the very next request. Then make a new one.

## 3. Connect your tool

### Claude Code / Claude Desktop

Save the token to a file, then tell Claude where it is:

1. Open Notepad, paste the token, save as `C:\Users\<you>\.zefbom-token`
2. Say: *"my ZEF BOM token is in C:\Users\<you>\.zefbom-token"*

Claude can then call the API directly. Nothing else to set up.

### Claude chat, Claude on your phone (connector)

claude.ai and the phone app cannot run commands, so the file above does not help them. They
connect through a **connector**:

Claude → **Settings → Connectors → Add custom connector**

**First screen**

| Field | Value |
|---|---|
| Name | `ZEF BOM` |
| MCP server URL | `https://zef-bom.up.railway.app/api/mcp` |

**Second screen** — this is where it goes wrong if you rush it.

- **Authentication** → choose **No sign-in**. Not "Sign in now", even though it is marked
  *Detected*: Claude sees our `401` and assumes OAuth, which this server does not do. The
  "No sign-in" wording is the accurate one — *"for servers that use an API key instead of
  OAuth"*.
- **Request headers** → **+ Add header**, name `Authorization`, value `Bearer ` followed by
  your token. The `Bearer ` prefix is required; without it the server refuses the connection.

Then ask BOM questions from anywhere. Nothing further to configure and nothing to explain to
Claude: the connector hands it the eight tools with their descriptions, plus a note that costs
exist at three volumes and that coverage below 100% makes a total a floor rather than a price.

### ChatGPT (custom GPT)

ChatGPT's normal browsing **cannot** do this — it cannot attach the token to a request. You
need a custom GPT with an Action:

1. ChatGPT → **Create a GPT** → **Configure** → **Actions**
2. Import or paste `docs/api/zef-bom-readonly-openapi.json` from this repository
3. **Authentication** → **API Key**, Auth Type **Bearer**, paste your token
4. Save

That file lists ten read endpoints and no write endpoints at all, so the GPT cannot even
attempt a change.

> Anything the GPT reads is sent to OpenAI. That may be fine — decide it deliberately, and
> use a token labelled for that GPT so you can revoke it on its own.

### Codex, Cursor, a script, anything else

It is an ordinary HTTP API. Send the token as a bearer header:

```bash
curl -H "Authorization: Bearer $ZEFBOM_TOKEN" \
     "https://zef-bom.up.railway.app/api/costing/summary?root=AEC066A"
```

```python
import os, requests
r = requests.get(
    "https://zef-bom.up.railway.app/api/costing/summary",
    params={"root": "AEC066A"},
    headers={"Authorization": f"Bearer {os.environ['ZEFBOM_TOKEN']}"},
    timeout=30,
)
print(r.json())
```

## 4. What you can ask

| Question | Endpoint |
|---|---|
| What does this BOM cost at each volume? | `/api/costing/summary?root=AEC066A` |
| Where does the cost sit? | `/api/costing/breakdown?root=AEC066A&volume=10000` |
| Show me the BOM structure | `/api/tree?root=AEC066A` |
| What do we need to buy, in total? | `/api/flat?root=AEC066A` |
| What data is still missing? | `/api/pending` |
| Find a part | `/api/items?q=valve` |
| Where is this part used? | `/api/items/{item_id}/where-used` |
| What are its min/likely/max costs? | `/api/items/{item_id}/decided-cost` |

## 5. What it cannot do

- **It cannot change anything.** Every create, edit and delete is refused, whoever the token
  belongs to and whatever tool is holding it. Editing needs a browser sign-in.
- **It cannot reach the admin screens** — the user list, and the full database export.
- **It sees exactly what its account sees**, no more.

## 6. If something looks wrong

- `401` — the token is wrong, expired or revoked. Make a new one.
- `403` on a read — the account is not on the allowlist. Ask an admin.
- `403` on a write — working as intended. Tokens never write.

Questions: ask Mrigank.
