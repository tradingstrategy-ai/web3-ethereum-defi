# Claude tuning

## Setting

Default permissions ask for confirmations repeatedly, enable safe permissions with `.claude/settings.json` to allow Claude to work independently.

## Skills

- [See example skill descriptions here](./.claude/skills)

## Github MCP server

- Github MCP server offers code search, other tools, but we might want to limit to read-only access for now
- Get [Personal Access Token (PAT) here](https://github.com/settings/tokens)
- Generate new token (Classic)
- Add read-only scopes: `repo`, `notifications`, `read:project`, `read:org`, `read:user`, `read:discussion`,

To add:

```shell
claude mcp add --transport http github https://api.githubcopilot.com/mcp -H "Authorization: Bearer YOUR_GITHUB_PAT"
```

Test it:

```shell
claude "List my GitHub repositories"
```

## Checking web pages under development in Svelte

Install Playwright MCP:

```shell
claude mcp add playwright npx @playwright/mcp@latest
```

After this you can run the `vite` dev server and ask Claude to preview pages on it.
