"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Plus, RotateCcw, Trash2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { CategoryCatalogResponse, CategoryDefinition } from "@/types/api";
import { api } from "@/utils/api";
import { CATEGORY_ENDPOINTS } from "@/utils/api-endpoints";

interface EditableCategory extends CategoryDefinition {
  id: string;
}

interface CategoryEditorProps {
  catalog: CategoryCatalogResponse;
  disabled: boolean;
  onSaved: (catalog: CategoryCatalogResponse) => void;
}

const MAX_CATEGORIES = 50;
const CATEGORY_NAME = /^[a-z][a-z0-9_]*$/;
const VALIDATION_MESSAGE_ID = "category-catalog-validation-message";

function makeRows(catalog: CategoryDefinition[]): EditableCategory[] {
  return catalog.map((definition, index) => ({
    ...definition,
    id: `saved-${index}-${definition.name}`,
  }));
}

function validationMessage(rows: EditableCategory[]): string | null {
  if (rows.length === 0) {
    return "Use Restore defaults to replace your catalog with the default categories.";
  }

  const names = new Set<string>();
  for (const row of rows) {
    const name = row.name.trim();
    const description = row.description.trim();
    if (!CATEGORY_NAME.test(name) || name.length > 64) {
      return "Category names must start with a lowercase letter and use only lowercase letters, numbers, and underscores.";
    }
    if (!description || description.length > 500) {
      return "Each category needs a description of up to 500 characters.";
    }
    if (names.has(name)) {
      return "Each category name must be unique.";
    }
    names.add(name);
  }

  return null;
}

export function CategoryEditor({
  catalog,
  disabled,
  onSaved,
}: CategoryEditorProps) {
  const nextRowId = useRef(0);
  const [rows, setRows] = useState<EditableCategory[]>(() =>
    makeRows(catalog.saved),
  );
  const [isSaving, setIsSaving] = useState(false);
  const [restoreOpen, setRestoreOpen] = useState(false);

  useEffect(() => {
    setRows(makeRows(catalog.saved));
  }, [catalog.saved]);

  const error = useMemo(() => validationMessage(rows), [rows]);
  const controlsDisabled = disabled || isSaving;

  const updateRow = (
    id: string,
    field: keyof CategoryDefinition,
    value: string,
  ) => {
    setRows((current) =>
      current.map((row) => (row.id === id ? { ...row, [field]: value } : row)),
    );
  };

  const moveRow = (index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    setRows((current) => {
      if (nextIndex < 0 || nextIndex >= current.length) return current;
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };

  const save = async (definitions: CategoryDefinition[]) => {
    setIsSaving(true);
    try {
      const response = await api.put<CategoryCatalogResponse>(
        CATEGORY_ENDPOINTS.BASE,
        definitions,
      );
      onSaved(response.data);
      toast({
        title: definitions.length
          ? "Category catalog saved"
          : "Default categories restored",
        variant: "success",
      });
      return true;
    } catch (requestError) {
      toast({
        title: "Failed to save category catalog",
        description: getErrorMessage(requestError),
        variant: "destructive",
      });
      return false;
    } finally {
      setIsSaving(false);
    }
  };

  const handleSave = async () => {
    if (error) return;
    await save(
      rows.map(({ name, description }) => ({
        name: name.trim(),
        description: description.trim(),
      })),
    );
  };

  const handleRestore = async () => {
    const restored = await save([]);
    if (restored) setRestoreOpen(false);
  };

  return (
    <Card className="border-memBorder-primary">
      <CardHeader className="gap-2">
        <CardTitle className="text-sm">Your catalog</CardTitle>
        <p className="text-sm text-onSurface-default-tertiary">
          Define the ordered labels used for new memory classification.
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-md border border-memBorder-primary bg-surface-default-fg-secondary p-3 text-sm text-onSurface-default-secondary">
          Catalog changes apply only to future classification. Existing memories
          keep their current labels until you explicitly reclassify them.
        </div>

        {rows.length === 0 ? (
          <p className="text-sm text-onSurface-default-tertiary">
            Your catalog is not saved yet. The default categories are active.
          </p>
        ) : (
          <div className="space-y-3">
            {rows.map((row, index) => (
              <div
                key={row.id}
                className="grid gap-3 rounded-lg border border-memBorder-primary p-3 md:grid-cols-[minmax(11rem,1fr)_minmax(16rem,2fr)_auto]"
              >
                <div className="space-y-1">
                  <Label className="sr-only" htmlFor={`${row.id}-name`}>
                    Category name {index + 1}
                  </Label>
                  <Input
                    id={`${row.id}-name`}
                    value={row.name}
                    onChange={(event) =>
                      updateRow(row.id, "name", event.target.value)
                    }
                    disabled={controlsDisabled}
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? VALIDATION_MESSAGE_ID : undefined}
                    placeholder="billing"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="sr-only" htmlFor={`${row.id}-description`}>
                    Category description {index + 1}
                  </Label>
                  <Textarea
                    id={`${row.id}-description`}
                    value={row.description}
                    onChange={(event) =>
                      updateRow(row.id, "description", event.target.value)
                    }
                    disabled={controlsDisabled}
                    aria-invalid={Boolean(error)}
                    aria-describedby={error ? VALIDATION_MESSAGE_ID : undefined}
                    placeholder="Invoices, payments, and account balances"
                    rows={2}
                  />
                </div>
                <div className="flex items-start gap-1 md:justify-end">
                  <Button
                    aria-label={`Move category ${row.name || index + 1} up`}
                    disabled={controlsDisabled || index === 0}
                    onClick={() => moveRow(index, -1)}
                    size="icon"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowUp className="size-4" />
                  </Button>
                  <Button
                    aria-label={`Move category ${row.name || index + 1} down`}
                    disabled={controlsDisabled || index === rows.length - 1}
                    onClick={() => moveRow(index, 1)}
                    size="icon"
                    type="button"
                    variant="ghost"
                  >
                    <ArrowDown className="size-4" />
                  </Button>
                  <Button
                    aria-label={`Remove category ${row.name || index + 1}`}
                    disabled={controlsDisabled}
                    onClick={() =>
                      setRows((current) =>
                        current.filter(
                          (currentRow) => currentRow.id !== row.id,
                        ),
                      )
                    }
                    size="icon"
                    type="button"
                    variant="ghost"
                  >
                    <Trash2 className="size-4 text-onSurface-danger-primary" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {error && (
          <p
            className="text-sm text-onSurface-danger-primary"
            id={VALIDATION_MESSAGE_ID}
            role="alert"
          >
            {error}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button
            disabled={controlsDisabled || rows.length >= MAX_CATEGORIES}
            onClick={() =>
              setRows((current) => [
                ...current,
                { id: `new-${nextRowId.current++}`, name: "", description: "" },
              ])
            }
            size="sm"
            type="button"
            variant="outline"
          >
            <Plus className="mr-1 size-4" /> Add category
          </Button>
          <Button
            disabled={controlsDisabled || Boolean(error)}
            onClick={handleSave}
            size="sm"
            type="button"
          >
            {isSaving ? "Saving..." : "Save catalog"}
          </Button>
          <AlertDialog open={restoreOpen} onOpenChange={setRestoreOpen}>
            <AlertDialogTrigger asChild>
              <Button
                disabled={controlsDisabled || catalog.saved.length === 0}
                size="sm"
                type="button"
                variant="outline"
              >
                <RotateCcw className="mr-1 size-4" /> Restore defaults
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Restore default categories?</AlertDialogTitle>
                <AlertDialogDescription>
                  This replaces your saved catalog with the built-in defaults.
                  Existing memories will not be changed.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={isSaving}>
                  Cancel
                </AlertDialogCancel>
                <AlertDialogAction
                  className="bg-onSurface-danger-primary hover:bg-onSurface-danger-secondary"
                  disabled={isSaving}
                  onClick={(event) => {
                    event.preventDefault();
                    void handleRestore();
                  }}
                >
                  {isSaving ? "Restoring..." : "Restore defaults"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </CardContent>
    </Card>
  );
}
