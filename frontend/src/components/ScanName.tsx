import { useState, type FormEvent } from 'react';
import { Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useRenameScan } from '@/hooks/useScan';
import type { ScanSummary } from '@/types/scan';

/**
 * The scan's title: its name if it has one, otherwise when it ran.
 *
 * Naming is optional and stays that way — an unnamed scan shows a timestamp,
 * which is already a perfectly good identity, rather than an empty field
 * asking to be filled in.
 */
export function ScanName({ scan }: { scan: ScanSummary }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(scan.name ?? '');
  const rename = useRenameScan(scan.id);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    rename.mutate(draft.trim() || null, { onSuccess: () => setEditing(false) });
  }

  if (editing) {
    return (
      <form onSubmit={handleSubmit} className="flex flex-wrap items-center gap-2">
        <Input
          autoFocus
          aria-label="Scan name"
          placeholder="Before the refactor"
          className="h-9 max-w-xs"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button type="submit" size="sm" disabled={rename.isPending}>
          {rename.isPending ? 'Saving…' : 'Save'}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            setDraft(scan.name ?? '');
            setEditing(false);
          }}
        >
          Cancel
        </Button>
      </form>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <h1 className="text-2xl font-semibold">{scan.name ?? 'Scan results'}</h1>
      <Button
        variant="ghost"
        size="icon"
        aria-label={scan.name ? 'Rename this scan' : 'Name this scan'}
        onClick={() => setEditing(true)}
      >
        <Pencil className="text-muted-foreground" />
      </Button>
    </div>
  );
}
