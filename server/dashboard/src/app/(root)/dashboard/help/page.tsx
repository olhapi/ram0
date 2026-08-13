"use client";

import Link from "next/link";
import CopyButton from "@/components/misc/copy-button";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { agentInstalls, mcpUrl, skillInstallCommand } from "./help-content";

const apiUrl = process.env.NEXT_PUBLIC_API_URL;
const endpoint = mcpUrl(apiUrl);
const installs = agentInstalls(apiUrl);
const tools = [
  "remember",
  "search_memories",
  "list_memories",
  "get_memory",
  "update_memory",
  "forget_memory",
];

function Command({ label, value }: { label: string; value: string }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md bg-surface-default-secondary p-4 pr-12 text-xs leading-5">
        <code>{value}</code>
      </pre>
      <CopyButton ariaLabel={label} textToCopy={value} />
    </div>
  );
}

export default function HelpPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold font-fustat">Help</h1>
        <p className="text-sm text-onSurface-default-tertiary">
          Connect your coding agent to account-scoped Ram0 memory.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Connect Agents</CardTitle>
          <CardDescription>
            Install Ram0 from its public marketplace, then store the URL and API
            key permanently with protected file permissions.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button asChild size="sm" variant="outline">
            <Link href="/dashboard/api-keys">Create an API Key</Link>
          </Button>
          {!endpoint ? (
            <p className="rounded-lg border border-onSurface-danger-primary/40 p-3 text-sm text-onSurface-danger-primary">
              Setup is unavailable until an operator configures
              NEXT_PUBLIC_API_URL. No placeholder command is shown.
            </p>
          ) : (
            <Tabs defaultValue={installs[0].id}>
              <TabsList
                aria-label="Ram0 client setup"
                className="h-auto flex-wrap justify-start gap-1"
              >
                {installs.map((install) => (
                  <TabsTrigger key={install.id} value={install.id}>
                    {install.name}
                  </TabsTrigger>
                ))}
              </TabsList>
              {installs.map((install) => (
                <TabsContent key={install.id} value={install.id}>
                  <div className="space-y-5 rounded-lg border border-memBorder-primary p-4">
                    <section className="space-y-3">
                      <h2 className="font-medium">Install the Ram0 plugin</h2>
                      <p className="text-sm text-onSurface-default-secondary">
                        Install from the public marketplace. No Ram0 source
                        checkout is required.
                      </p>
                      <Command
                        label={`Copy ${install.name} Ram0 plugin installation`}
                        value={install.pluginInstall}
                      />
                      <p className="text-sm text-onSurface-default-tertiary">
                        {install.pluginNote}
                      </p>
                    </section>

                    <section className="space-y-3 border-t border-memBorder-primary pt-5">
                      <h2 className="font-medium">Update an existing plugin</h2>
                      <p className="text-sm text-onSurface-default-secondary">
                        Refresh the installed marketplace plugin, then restart
                        your client. Your persistent Ram0 configuration is
                        preserved.
                      </p>
                      <Command
                        label={`Copy ${install.name} Ram0 plugin update`}
                        value={install.pluginUpdate}
                      />
                    </section>

                    <section className="space-y-3 border-t border-memBorder-primary pt-5">
                      <h2 className="font-medium">Permanent Ram0 setup</h2>
                      <p className="text-sm text-onSurface-default-secondary">
                        After restarting the client, run setup. It prompts for
                        the API key without echoing it and stores only the URL
                        and key in <code>~/.config/ram0/config.json</code> with
                        mode 0600.
                      </p>
                      <Command
                        label={`Copy ${install.name} persistent Ram0 setup`}
                        value={install.persistentSetup}
                      />
                      <Command
                        label={`Copy ${install.name} Ram0 configuration test`}
                        value={install.configVerify}
                      />
                    </section>

                    <section className="space-y-2 border-t border-memBorder-primary pt-5">
                      <h2 className="font-medium">Migration</h2>
                      <p className="text-sm text-onSurface-default-secondary">
                        {install.migration}
                      </p>
                      <h2 className="pt-2 font-medium">Troubleshooting</h2>
                      <ul className="list-disc space-y-1 pl-5 text-sm text-onSurface-default-secondary">
                        {install.troubleshooting.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </section>
                  </div>
                </TabsContent>
              ))}
            </Tabs>
          )}
          <p className="text-xs text-onSurface-default-tertiary">
            The API key is shown once on the API Keys page. Never paste it into
            MCP JSON, plugin manifests, source code, or this dashboard.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Install Automation and Skills</CardTitle>
          <CardDescription>
            Skills-only adds memory-use guidance without connecting MCP or
            installing hooks.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Command
            label="Copy Ram0 memory skill setup"
            value={skillInstallCommand}
          />
          <p className="text-sm text-onSurface-default-tertiary">
            Choose direct MCP separately if you use skills-only.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Using Ram0</CardTitle>
          <CardDescription>
            Each API key accesses only its owner&apos;s account-wide memory
            namespace.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ul className="grid gap-2 text-sm sm:grid-cols-2">
            {tools.map((tool) => (
              <li
                key={tool}
                className="rounded-md bg-surface-default-secondary px-3 py-2 font-mono text-xs"
              >
                {tool}
              </li>
            ))}
          </ul>
          <p className="text-sm text-onSurface-default-secondary">
            Ask your agent to remember a genuine preference, start a new task,
            then search for it to verify cross-task recall.
          </p>
          <a
            className="text-sm underline underline-offset-4"
            href="https://docs.mem0.ai/open-source/ram0-mcp"
          >
            Mem0 MCP guide
          </a>
        </CardContent>
      </Card>
    </div>
  );
}
