"use client";

import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { format } from "date-fns";
import { CategoriesDisplay } from "@/components/ui/categories-display";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { UpgradeBanner } from "@/components/self-hosted/upgrade-banner";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { api } from "@/utils/api";
import { CATEGORY_ENDPOINTS, MEMORY_ENDPOINTS } from "@/utils/api-endpoints";
import { useApiQuery } from "@/hooks/use-api-query";
import { CategoryCatalogResponse, Memory } from "@/types/api";

const PAGE_SIZE = 20;
// Keep in sync with ALL_MEMORIES_LIMIT in server/main.py.
const MEMORY_FETCH_LIMIT = 1000;

function sameCategories(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((name, index) => name === right[index])
  );
}

function memoryCategories(memory: Memory): string[] {
  return Array.isArray(memory.categories)
    ? memory.categories.filter(
        (category): category is string => typeof category === "string",
      )
    : [];
}

function memoryCategoryStatus(memory: Memory): Memory["category_status"] {
  switch (memory.category_status) {
    case "pending":
    case "completed":
    case "failed":
    case "unclassified":
      return memory.category_status;
    default:
      return "unclassified";
  }
}

function CategoryStateBadge({ status }: { status: Memory["category_status"] }) {
  if (status === "completed") return null;
  if (status === "pending") return <Badge variant="secondary">Pending</Badge>;
  if (status === "failed") return <Badge variant="destructive">Failed</Badge>;
  return (
    <Badge
      className="border-yellow-500/60 text-yellow-700 dark:text-yellow-300"
      variant="outline"
    >
      Unclassified
    </Badge>
  );
}

function MemoryCategories({
  memory,
  showAll = false,
}: {
  memory: Memory;
  showAll?: boolean;
}) {
  const categories = memoryCategories(memory);
  const status = memoryCategoryStatus(memory);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {categories.length > 0 ? (
        <CategoriesDisplay
          categories={categories}
          showAllCategories={showAll}
        />
      ) : status === "completed" ? (
        <span className="text-sm text-onSurface-default-tertiary">
          No categories
        </span>
      ) : null}
      <CategoryStateBadge status={status} />
    </div>
  );
}

export default function MemoriesPage() {
  const [userId, setUserId] = useState("");
  const [selectedMemory, setSelectedMemory] = useState<Memory | null>(null);
  const [memoryToDelete, setMemoryToDelete] = useState<Memory | null>(null);
  const [page, setPage] = useState(0);
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [appliedCategories, setAppliedCategories] = useState<string[]>([]);
  const hasAppliedCategoryFilter = useRef(false);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";

  const { data: categoryCatalog } = useApiQuery<CategoryCatalogResponse>(
    async () => {
      const response = await api.get<CategoryCatalogResponse>(
        CATEGORY_ENDPOINTS.BASE,
      );
      return response.data;
    },
    { errorToast: "Failed to load category catalog" },
  );

  const {
    data: memories = [],
    isLoading,
    refetch,
  } = useApiQuery<Memory[]>(
    async () => {
      const params = new URLSearchParams();
      if (userId.trim()) params.append("user_id", userId.trim());
      params.append("top_k", String(MEMORY_FETCH_LIMIT));
      appliedCategories.forEach((name) => params.append("categories", name));
      const res = await api.get(
        `${MEMORY_ENDPOINTS.BASE}?${params.toString()}`,
      );
      const raw = res.data?.results ?? res.data ?? [];
      return Array.isArray(raw) ? raw : [];
    },
    { errorToast: "Failed to load memories", initialData: [] },
  );

  useEffect(() => {
    if (!hasAppliedCategoryFilter.current) {
      hasAppliedCategoryFilter.current = true;
      return;
    }
    setPage(0);
    void refetch();
  }, [appliedCategories, refetch]);

  const activeCategories = categoryCatalog?.active ?? [];

  const toggleCategory = (name: string) => {
    setSelectedCategories((current) =>
      current.includes(name)
        ? current.filter((category) => category !== name)
        : [...current, name],
    );
  };

  const applyCategoryFilter = () => {
    if (sameCategories(selectedCategories, appliedCategories)) return;
    setAppliedCategories(selectedCategories);
  };

  const totalPages = Math.ceil(memories.length / PAGE_SIZE);
  const paginatedMemories = memories.slice(
    page * PAGE_SIZE,
    (page + 1) * PAGE_SIZE,
  );

  const handleDelete = async () => {
    if (!memoryToDelete) return;
    try {
      await api.delete(MEMORY_ENDPOINTS.BY_ID(memoryToDelete.id));
      toast({ title: "Memory deleted", variant: "success" });
      if (selectedMemory?.id === memoryToDelete.id) setSelectedMemory(null);
      setMemoryToDelete(null);
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to delete memory",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const columns = [
    {
      key: "memory" as keyof Memory,
      label: "Content",
      width: 400,
      render: (value: string) => (
        <span className="line-clamp-2 text-sm">{value}</span>
      ),
    },
    { key: "user_id" as keyof Memory, label: "User", width: 100 },
    { key: "agent_id" as keyof Memory, label: "Agent", width: 100 },
    {
      key: "created_at" as keyof Memory,
      label: "Created",
      width: 120,
      render: (value: string) =>
        value ? format(new Date(value), "MMM d, yyyy") : "--",
    },
    {
      key: "categories" as keyof Memory,
      label: "Categories",
      width: 200,
      render: (_value: Memory["categories"], row: Memory) => (
        <MemoryCategories memory={row} />
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold font-fustat">Memories</h1>

      {memories.length >= MEMORY_FETCH_LIMIT && (
        <UpgradeBanner
          id="memories-1k"
          message="1,000+ memories stored. Categories can help organize them."
          ctaLabel="Explore Cloud"
          ctaUrl="https://app.mem0.ai?utm_source=oss&utm_medium=dashboard-memories"
          variant="cloud"
        />
      )}

      <div className="flex flex-wrap gap-3">
        <Input
          placeholder="Filter by User ID (optional)"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              setPage(0);
              refetch();
            }
          }}
          className="w-64"
        />
        <Popover>
          <PopoverTrigger asChild>
            <Button size="sm" type="button" variant="outline">
              Categories ({selectedCategories.length})
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 space-y-3 p-3">
            <div>
              <p className="text-sm font-medium">Filter categories</p>
              <p className="text-xs text-onSurface-default-tertiary">
                Show memories matching any selected active category.
              </p>
            </div>
            {activeCategories.length === 0 ? (
              <p className="text-sm text-onSurface-default-tertiary">
                No active categories are available.
              </p>
            ) : (
              <div className="max-h-56 space-y-2 overflow-y-auto">
                {activeCategories.map((category) => (
                  <div className="flex items-center gap-2" key={category.name}>
                    <Checkbox
                      checked={selectedCategories.includes(category.name)}
                      id={`memory-category-${category.name}`}
                      onCheckedChange={() => toggleCategory(category.name)}
                    />
                    <Label
                      className="cursor-pointer text-sm"
                      htmlFor={`memory-category-${category.name}`}
                    >
                      {category.name}
                    </Label>
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                disabled={sameCategories(selectedCategories, appliedCategories)}
                onClick={applyCategoryFilter}
                size="sm"
                type="button"
              >
                Apply filter
              </Button>
            </div>
          </PopoverContent>
        </Popover>
      </div>

      {isLoading ? (
        <TableSkeleton rows={5} columns={5} />
      ) : memories.length === 0 ? (
        <EmptyState
          title="No memories yet"
          description="Create your first memory by sending a POST /memories request."
        >
          <pre className="text-xs text-left bg-surface-default-secondary p-3 rounded font-mono overflow-x-auto mt-3 max-w-lg">
            {`curl -X POST ${apiUrl}/memories \\
  -H "X-API-Key: <your-key>" \\
  -H "Content-Type: application/json" \\
  -d '{"messages": [{"role": "user", "content": "I like hiking"}], "user_id": "alice"}'`}
          </pre>
          <a
            href="https://docs.mem0.ai/open-source/features/rest-api#memory-operations"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-onSurface-default-tertiary underline underline-offset-4 hover:text-onSurface-default-primary mt-2"
          >
            REST API reference
          </a>
        </EmptyState>
      ) : (
        <>
          <Card className="border-memBorder-primary overflow-hidden">
            <DataTable
              data={paginatedMemories}
              columns={columns}
              getRowKey={(row) => row.id}
              onRowClick={(row) => setSelectedMemory(row)}
              getRowClassName={(row) =>
                selectedMemory?.id === row.id
                  ? "bg-surface-default-tertiary"
                  : undefined
              }
            />
          </Card>
          {totalPages > 1 && (
            <div className="flex items-center justify-between text-sm text-onSurface-default-tertiary">
              <span>
                {page * PAGE_SIZE + 1}–
                {Math.min((page + 1) * PAGE_SIZE, memories.length)} of{" "}
                {memories.length}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </Button>
              </div>
            </div>
          )}
        </>
      )}

      <Sheet
        open={!!selectedMemory}
        onOpenChange={(open) => {
          if (!open) setSelectedMemory(null);
        }}
      >
        <SheetContent className="sm:max-w-md">
          <SheetHeader>
            <SheetTitle>Memory Detail</SheetTitle>
            <SheetDescription className="sr-only">
              View memory content and metadata
            </SheetDescription>
          </SheetHeader>
          {selectedMemory && (
            <div className="mt-6 space-y-4">
              <div className="space-y-1">
                <Label className="text-xs text-onSurface-default-tertiary">
                  Content
                </Label>
                <p className="text-sm">{selectedMemory.memory}</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <Label className="text-xs text-onSurface-default-tertiary">
                    ID
                  </Label>
                  <p className="text-xs font-mono break-all">
                    {selectedMemory.id}
                  </p>
                </div>
                {selectedMemory.user_id && (
                  <div className="space-y-1">
                    <Label className="text-xs text-onSurface-default-tertiary">
                      User
                    </Label>
                    <p className="text-sm">{selectedMemory.user_id}</p>
                  </div>
                )}
                {selectedMemory.agent_id && (
                  <div className="space-y-1">
                    <Label className="text-xs text-onSurface-default-tertiary">
                      Agent
                    </Label>
                    <p className="text-sm">{selectedMemory.agent_id}</p>
                  </div>
                )}
                {selectedMemory.created_at && (
                  <div className="space-y-1">
                    <Label className="text-xs text-onSurface-default-tertiary">
                      Created
                    </Label>
                    <p className="text-sm">
                      {new Date(selectedMemory.created_at).toLocaleString()}
                    </p>
                  </div>
                )}
              </div>
              <div className="space-y-1">
                <Label className="text-xs text-onSurface-default-tertiary">
                  Categories
                </Label>
                <MemoryCategories memory={selectedMemory} showAll />
              </div>
              <Button
                variant="outline"
                size="sm"
                className="text-onSurface-danger-primary"
                onClick={() => setMemoryToDelete(selectedMemory)}
              >
                <Trash2 className="size-3.5 mr-1" />
                Delete memory
              </Button>
            </div>
          )}
        </SheetContent>
      </Sheet>

      <DeleteConfirmationModal
        isOpen={!!memoryToDelete}
        onClose={() => setMemoryToDelete(null)}
        onConfirm={handleDelete}
        title="Delete memory"
        description="This memory will be permanently removed. This cannot be undone."
        itemName={memoryToDelete?.id ?? ""}
        confirmButtonText="Delete"
      />
    </div>
  );
}
