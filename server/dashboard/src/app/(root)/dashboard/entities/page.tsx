"use client";

// Modified for Ram0; see NOTICE and repository history.

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { format } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { EmptyState } from "@/components/self-hosted/empty-state";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import { toast } from "@/components/ui/use-toast";
import { api } from "@/utils/api";
import { ENTITY_ENDPOINTS } from "@/utils/api-endpoints";
import { getErrorMessage } from "@/lib/error-message";
import { useApiQuery } from "@/hooks/use-api-query";
import { Entity } from "@/types/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { EntityType } from "@/types/api";

type EntityFilter = "all" | EntityType;

export default function EntitiesPage() {
  const [entityToDelete, setEntityToDelete] = useState<Entity | null>(null);
  const [entityFilter, setEntityFilter] = useState<EntityFilter>("all");

  const {
    data: entities = [],
    isLoading,
    refetch,
  } = useApiQuery<Entity[]>(
    async () => {
      const res = await api.get<Entity[]>(ENTITY_ENDPOINTS.BASE);
      return res.data ?? [];
    },
    { errorToast: "Failed to load entities", initialData: [] },
  );

  const handleDelete = async () => {
    if (!entityToDelete) return;
    try {
      await api.delete(
        ENTITY_ENDPOINTS.BY_ID(entityToDelete.type, entityToDelete.id),
      );
      toast({ title: "Entity deleted", variant: "success" });
      setEntityToDelete(null);
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to delete entity",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const columns = [
    {
      key: "type" as keyof Entity,
      label: "Type",
      width: 100,
      render: (value: Entity["type"]) => (
        <Badge variant="outline" className="capitalize">
          {value}
        </Badge>
      ),
    },
    {
      key: "id" as keyof Entity,
      label: "ID",
      width: 280,
      render: (value: string) => (
        <span className="font-mono text-sm truncate">{value}</span>
      ),
    },
    {
      key: "total_memories" as keyof Entity,
      label: "Memories",
      width: 100,
      align: "right" as const,
    },
    {
      key: "updated_at" as keyof Entity,
      label: "Last Active",
      width: 140,
      render: (value: string | null) =>
        value ? format(new Date(value), "MMM d, yyyy") : "--",
    },
    {
      key: "id" as keyof Entity,
      label: "",
      width: 40,
      render: (_: string, row: Entity) => (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setEntityToDelete(row)}
          className="size-7"
        >
          <Trash2 className="size-3.5 text-onSurface-danger-primary" />
        </Button>
      ),
    },
  ];

  const visibleEntities =
    entityFilter === "all"
      ? entities
      : entities.filter((entity) => entity.type === entityFilter);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold font-fustat">Entities</h1>

      <Select
        value={entityFilter}
        onValueChange={(value) => setEntityFilter(value as EntityFilter)}
      >
        <SelectTrigger className="w-48" aria-label="Filter entities by type">
          <SelectValue placeholder="All entities" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All entities</SelectItem>
          <SelectItem value="app">Projects</SelectItem>
          <SelectItem value="agent">Agents</SelectItem>
          <SelectItem value="run">Runs</SelectItem>
        </SelectContent>
      </Select>

      {isLoading ? (
        <TableSkeleton rows={5} columns={5} />
      ) : entities.length === 0 ? (
        <EmptyState
          title="No entities yet"
          description="Projects, agents, and runs appear once scoped memories are stored."
        />
      ) : visibleEntities.length === 0 ? (
        <EmptyState
          title="No matching entities"
          description="No memories use this scope yet."
        />
      ) : (
        <Card className="border-memBorder-primary overflow-hidden">
          <DataTable
            data={visibleEntities}
            columns={columns}
            getRowKey={(row) => `${row.type}:${row.id}`}
          />
        </Card>
      )}

      <DeleteConfirmationModal
        isOpen={!!entityToDelete}
        onClose={() => setEntityToDelete(null)}
        onConfirm={handleDelete}
        title="Delete entity"
        description="All memories associated with this entity will be permanently removed. This cannot be undone."
        itemName={entityToDelete?.id ?? ""}
        confirmButtonText="Delete"
      />
    </div>
  );
}
