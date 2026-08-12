"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import { useApiQuery } from "@/hooks/use-api-query";
import { CategoryCatalogResponse, CategoryDefinition } from "@/types/api";
import { api } from "@/utils/api";
import { CATEGORY_ENDPOINTS } from "@/utils/api-endpoints";
import { CategoryEditor } from "./category-editor";
import { ReclassificationPanel } from "./reclassification-panel";

export default function CategoriesPage() {
  const [catalog, setCatalog] = useState<CategoryCatalogResponse>();
  const {
    data: fetchedCatalog,
    isLoading,
    error,
    refetch,
  } = useApiQuery<CategoryCatalogResponse>(
    async () => {
      const response = await api.get<CategoryCatalogResponse>(
        CATEGORY_ENDPOINTS.BASE,
      );
      return response.data;
    },
    { errorToast: "Failed to load category catalog" },
  );

  useEffect(() => {
    if (fetchedCatalog) setCatalog(fetchedCatalog);
  }, [fetchedCatalog]);

  const displayedCatalog = catalog ?? fetchedCatalog;
  const retired = displayedCatalog ? displayedCatalog.retired : [];

  const columns = [
    {
      key: "name" as keyof CategoryDefinition,
      label: "Category",
      width: 150,
      render: (value: string) => (
        <span className="font-mono text-sm">{value}</span>
      ),
    },
    {
      key: "description" as keyof CategoryDefinition,
      label: "Description",
      width: 360,
      render: (value: string) => (
        <span className="text-sm text-onSurface-default-secondary">
          {value}
        </span>
      ),
    },
    {
      key: "name" as keyof CategoryDefinition,
      label: "Memories",
      align: "right" as const,
      width: 90,
      render: (value: string) => String(displayedCatalog?.counts[value] ?? 0),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold font-fustat">Categories</h1>
            {displayedCatalog && (
              <Badge variant="outline">
                {displayedCatalog.source === "defaults"
                  ? "Defaults"
                  : "Your catalog"}
              </Badge>
            )}
          </div>
          <p className="text-sm text-onSurface-default-tertiary">
            Organize new memories with an ordered, model-driven category
            catalog.
          </p>
          {displayedCatalog && isLoading && (
            <p className="text-xs text-onSurface-default-tertiary">
              Refreshing catalog...
            </p>
          )}
        </div>
        <Button
          aria-label="Refresh category catalog"
          disabled={isLoading}
          onClick={() => void refetch()}
          size="sm"
          type="button"
          variant="outline"
        >
          <RefreshCw className="mr-1 size-4" /> Refresh
        </Button>
      </div>

      {!displayedCatalog && isLoading ? (
        <Card className="border-memBorder-primary overflow-hidden">
          <TableSkeleton rows={5} columns={3} />
        </Card>
      ) : !displayedCatalog ? (
        <EmptyState
          title="Could not load categories"
          description={
            error || "The category catalog is unavailable right now."
          }
        >
          <Button
            className="mt-4"
            onClick={() => void refetch()}
            size="sm"
            type="button"
          >
            Try again
          </Button>
        </EmptyState>
      ) : (
        <>
          <Card className="border-memBorder-primary overflow-hidden">
            <CardHeader>
              <CardTitle className="text-sm">Active categories</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {displayedCatalog.active.length === 0 ? (
                <EmptyState
                  title="No active categories"
                  description="Restore defaults or save your catalog to classify new memories."
                />
              ) : (
                <DataTable
                  data={displayedCatalog.active}
                  columns={columns}
                  getRowKey={(row) => row.name}
                />
              )}
            </CardContent>
          </Card>

          {retired.length > 0 && (
            <Card className="border-onSurface-danger-primary/40 bg-onSurface-danger-primary/5">
              <CardHeader className="flex-row items-center gap-2 space-y-0">
                <AlertTriangle className="size-4 text-onSurface-danger-primary" />
                <CardTitle className="text-sm">
                  Retired category labels
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="mb-3 text-sm text-onSurface-default-secondary">
                  These labels remain on historical memories but are not in the
                  active catalog.
                </p>
                <div className="flex flex-wrap gap-2">
                  {retired.map((category) => (
                    <Badge key={category.name} variant="outline">
                      {category.name} ({category.count})
                    </Badge>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          <>
            <CategoryEditor
              catalog={displayedCatalog}
              disabled={isLoading}
              onSaved={(savedCatalog) => {
                setCatalog(savedCatalog);
                void refetch();
              }}
            />
            <ReclassificationPanel
              disabled={isLoading}
              onReclassified={() => void refetch()}
            />
          </>
        </>
      )}
    </div>
  );
}
