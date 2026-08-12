#!/usr/bin/env bash
# Verify the public Ram0 automation documentation keeps its install and privacy contract.
set -euo pipefail

docs=(
  docs/open-source/ram0-mcp.mdx
  docs/integrations/ram0-plugin.mdx
  integrations/ram0-plugin/README.md
  server/README.md
)

require_in() {
  local file=$1
  local description=$2
  local expression=$3
  if ! rg -qi "$expression" "$file"; then
    echo "missing documentation in $file: $description" >&2
    exit 1
  fi
}

for file in "${docs[@]}"; do
  require_in "$file" "persistent configuration path" '~/.config/ram0/config.json'
  require_in "$file" "persistent setup command" 'ram0 setup'
  require_in "$file" "configuration test command" 'ram0 config test'
  require_in "$file" "key rotation command" 'ram0 config set-key'
  require_in "$file" "marketplace identity" 'ram0@ram0-plugins'
  require_in "$file" "Bearer authorization" 'Authorization: Bearer|Bearer authentication|Bearer credential'
  require_in "$file" "self-hosted endpoint placeholder" 'https://ram0\.example\.lan'
  require_in "$file" "direct MCP distinction" 'Direct MCP|six tools'
  require_in "$file" "full automation distinction" 'Full automation|automation plugin|automatic retrieval'
  require_in "$file" "key storage and logging privacy" 'never stored as memory(,| or) logged'
  require_in "$file" "telemetry exclusion" 'telemetry'
  require_in "$file" "third-party exclusion" 'third parties'
  require_in "$file" "migration guidance" 'mem0-plugins'
  require_in "$file" "permission repair" 'chmod 600'
  require_in "$file" "missing configuration guidance" 'missing config'
  require_in "$file" "unreachable endpoint guidance" 'unreachable endpoint'
done

for file in docs/integrations/ram0-plugin.mdx integrations/ram0-plugin/README.md server/README.md; do
  for client in Codex 'Claude Code' Cursor OpenCode; do
    require_in "$file" "$client installation" "$client"
  done
  require_in "$file" "raw prompt exclusion" 'raw prompts?'
  require_in "$file" "raw transcript exclusion" 'raw transcripts?'
  require_in "$file" "file dump exclusion" 'file dumps?'
  require_in "$file" "private owner category catalog" 'API-key owner|owner.*catalog|Categories are private'
  require_in "$file" "copied legacy category template" 'legacy.*template|template.*legacy'
  require_in "$file" "upstream Mem0 plugin boundary" 'upstream.*mem0|integrations/mem0-plugin'
done

for file in docs/integrations/ram0-plugin.mdx integrations/ram0-plugin/README.md; do
  require_in "$file" "canonical repository" 'https://github.com/olhapi/ram0.git'
  require_in "$file" "canonical Claude marketplace recipe" 'ram0@ram0-plugins'
  require_in "$file" "canonical OpenCode local package recipe" 'opencode plugin "file://\$PWD" --global'
  require_in "$file" "independent Ram0 skill install" 'npx skills add https://github.com/olhapi/ram0 --skill ram0-memory'
  require_in "$file" "search-before-write skill behavior" 'searches before writing|search before'
  require_in "$file" "Codex hook trust review" '/hooks'
done

if rg -n 'install_codex_hooks|codex_hooks[[:space:]]*=[[:space:]]*true' \
  docs/integrations/ram0-plugin.mdx integrations/ram0-plugin/README.md; then
  echo "documentation contains deprecated duplicate Codex hook installation" >&2
  exit 1
fi

if rg -n 'export[[:space:]]+RAM0_API_(URL|KEY)|launchctl|ram0@mem0-plugins' "${docs[@]}"; then
  echo "normal documentation contains deprecated ephemeral setup" >&2
  exit 1
fi

if rg -n "export[[:space:]]+RAM0_API_KEY=['\"]RAM0_API_KEY['\"]" "${docs[@]}"; then
  echo "documentation contains a copyable literal RAM0_API_KEY assignment" >&2
  exit 1
fi

if rg -n -i 'never (sends?|saves?).*API keys?' "${docs[@]}"; then
  echo "documentation contradicts Bearer credential transport" >&2
  exit 1
fi

if rg -n 'm0sk_[A-Za-z0-9_-]{16,}' "${docs[@]}"; then
  echo "documentation contains a credential-shaped value" >&2
  exit 1
fi

node --experimental-strip-types --test 'server/dashboard/src/app/(root)/dashboard/help/help-content.test.ts'

if rg -qi 'raw transcripts? (are )?saved|save raw transcripts?' "${docs[@]}"; then
  echo "documentation claims raw transcripts are saved" >&2
  exit 1
fi

echo "Ram0 plugin documentation contract: OK"
