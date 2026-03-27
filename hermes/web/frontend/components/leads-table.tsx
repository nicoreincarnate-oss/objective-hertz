'use client'

import { useState, useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Users, ArrowUpDown, ArrowUp, ArrowDown, Search, ChevronRight,
  CheckCircle2, XCircle, AlertTriangle, Filter,
} from 'lucide-react'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
  type ColumnFiltersState,
} from '@tanstack/react-table'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { authHeaders } from '@/hooks/use-token'
import { useToken } from '@/hooks/use-token'

interface Lead {
  id: number | string
  business_name: string
  email: string
  industry: string
  status: string
  lead_score: number
  created_at: string | null
}

interface LeadsTableProps {
  leads: Lead[]
}

const STATUS_CONFIG: Record<string, { label: string; color: string }> = {
  discovered:     { label: 'Discovered',    color: 'text-gold bg-gold/10 border-gold/20' },
  researched:     { label: 'Researched',    color: 'text-teal bg-teal/10 border-teal/20' },
  email_drafted:  { label: 'Drafted',       color: 'text-muted-foreground bg-muted border-border' },
  email_queued:   { label: 'Queued',        color: 'text-gold bg-gold/10 border-gold/20' },
  email_sent:     { label: 'Sent',          color: 'text-gold bg-gold/10 border-gold/20' },
  replied:        { label: 'Replied',       color: 'text-teal bg-teal/10 border-teal/20' },
  interested:     { label: 'Interested',    color: 'text-amber bg-amber/10 border-amber/20' },
  demo_built:     { label: 'Demo Built',    color: 'text-green bg-green/10 border-green/20' },
  proposal_sent:  { label: 'Proposal',      color: 'text-amber bg-amber/10 border-amber/20' },
  closed:         { label: 'Closed',        color: 'text-green bg-green/10 border-green/20' },
  building:       { label: 'Building',      color: 'text-teal bg-teal/10 border-teal/20' },
  deployed:       { label: 'Deployed',      color: 'text-green bg-green/10 border-green/20' },
  invoiced:       { label: 'Invoiced',      color: 'text-amber bg-amber/10 border-amber/20' },
  paid:           { label: 'Paid',          color: 'text-green bg-green/10 border-green/20' },
}

function getScoreColor(score: number) {
  if (score >= 90) return 'text-green'
  if (score >= 75) return 'text-gold'
  if (score >= 50) return 'text-amber'
  return 'text-muted-foreground'
}

function formatDate(dateString: string | null) {
  if (!dateString) return '—'
  const date = new Date(dateString)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function LeadsTable({ leads }: LeadsTableProps) {
  const { token } = useToken()
  const [sorting, setSorting] = useState<SortingState>([{ id: 'lead_score', desc: true }])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [selectedLead, setSelectedLead] = useState<Lead | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const columns = useMemo<ColumnDef<Lead>[]>(() => [
    {
      accessorKey: 'business_name',
      header: ({ column }) => (
        <button
          className="flex items-center gap-1 hover:text-foreground transition-colors"
          onClick={() => column.toggleSorting()}
        >
          Business
          {column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3" /> :
           column.getIsSorted() === 'desc' ? <ArrowDown className="w-3 h-3" /> :
           <ArrowUpDown className="w-3 h-3 opacity-40" />}
        </button>
      ),
      cell: ({ row }) => (
        <button
          onClick={() => setSelectedLead(row.original)}
          className="text-left group"
        >
          <div className="text-sm font-medium text-foreground group-hover:text-gold transition-colors">
            {row.original.business_name}
          </div>
          <div className="text-xs text-muted-foreground truncate max-w-[180px]">
            {row.original.email}
          </div>
        </button>
      ),
    },
    {
      accessorKey: 'industry',
      header: 'Industry',
      cell: ({ getValue }) => (
        <span className="text-sm text-muted-foreground">{getValue() as string}</span>
      ),
      meta: { className: 'hidden md:table-cell' },
    },
    {
      accessorKey: 'status',
      header: 'Status',
      cell: ({ getValue }) => {
        const status = getValue() as string
        const config = STATUS_CONFIG[status] || { label: status, color: 'text-muted-foreground bg-muted border-border' }
        return (
          <Badge variant="outline" className={`text-[10px] ${config.color}`}>
            {config.label}
          </Badge>
        )
      },
      filterFn: 'equals',
    },
    {
      accessorKey: 'lead_score',
      header: ({ column }) => (
        <button
          className="flex items-center gap-1 hover:text-foreground transition-colors"
          onClick={() => column.toggleSorting()}
        >
          Score
          {column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3" /> :
           column.getIsSorted() === 'desc' ? <ArrowDown className="w-3 h-3" /> :
           <ArrowUpDown className="w-3 h-3 opacity-40" />}
        </button>
      ),
      cell: ({ getValue }) => {
        const score = getValue() as number
        return <span className={`text-sm font-semibold tabular-nums ${getScoreColor(score)}`}>{score}</span>
      },
    },
    {
      accessorKey: 'created_at',
      header: ({ column }) => (
        <button
          className="flex items-center gap-1 hover:text-foreground transition-colors"
          onClick={() => column.toggleSorting()}
        >
          Date
          {column.getIsSorted() === 'asc' ? <ArrowUp className="w-3 h-3" /> :
           column.getIsSorted() === 'desc' ? <ArrowDown className="w-3 h-3" /> :
           <ArrowUpDown className="w-3 h-3 opacity-40" />}
        </button>
      ),
      cell: ({ getValue }) => (
        <span className="text-xs text-muted-foreground">{formatDate(getValue() as string | null)}</span>
      ),
      meta: { className: 'hidden sm:table-cell' },
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <button
            onClick={(e) => { e.stopPropagation(); handleAction(row.original.id, 'approve') }}
            className="p-1 rounded hover:bg-green/10 text-green/60 hover:text-green transition-colors"
            title="Approve"
          >
            <CheckCircle2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); handleAction(row.original.id, 'reject') }}
            className="p-1 rounded hover:bg-red/10 text-red/60 hover:text-red transition-colors"
            title="Reject"
          >
            <XCircle className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); handleAction(row.original.id, 'escalate') }}
            className="p-1 rounded hover:bg-amber/10 text-amber/60 hover:text-amber transition-colors"
            title="Escalate"
          >
            <AlertTriangle className="w-3.5 h-3.5" />
          </button>
        </div>
      ),
    },
  ], [])

  const table = useReactTable({
    data: leads,
    columns,
    state: { sorting, columnFilters, globalFilter },
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 10 } },
  })

  const handleAction = async (leadId: number | string, action: string) => {
    if (!token) return
    setActionLoading(`${leadId}-${action}`)
    try {
      await fetch(`/api/leads/${leadId}/action`, {
        method: 'POST',
        headers: { ...authHeaders(token), 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
    } finally {
      setActionLoading(null)
    }
  }

  // Get unique statuses for filter dropdown
  const uniqueStatuses = useMemo(() => {
    const statuses = new Set(leads.map(l => l.status))
    return Array.from(statuses).sort()
  }, [leads])

  return (
    <>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.6 }}
        className="glass-card-elevated hud-panel rounded-xl overflow-hidden"
      >
        {/* Header with search and filters */}
        <div className="p-5 md:p-6 border-b border-border">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground flex items-center gap-2">
              <Users className="w-5 h-5 text-gold" />
              Leads
              <span className="text-xs text-muted-foreground">({leads.length})</span>
            </h2>
          </div>

          {/* Search + filter row */}
          <div className="flex gap-3 flex-wrap">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                value={globalFilter}
                onChange={(e) => setGlobalFilter(e.target.value)}
                placeholder="Search business or email..."
                className="pl-9 bg-muted/50 border-border h-9 text-sm"
              />
            </div>
            <Select
              value={(table.getColumn('status')?.getFilterValue() as string) ?? 'all'}
              onValueChange={(v) => table.getColumn('status')?.setFilterValue(v === 'all' ? undefined : v)}
            >
              <SelectTrigger className="w-[140px] h-9 bg-muted/50 border-border text-sm">
                <SelectValue placeholder="All statuses" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {uniqueStatuses.map(s => (
                  <SelectItem key={s} value={s}>
                    {STATUS_CONFIG[s]?.label ?? s}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              {table.getHeaderGroups().map(headerGroup => (
                <tr key={headerGroup.id} className="border-b border-border">
                  {headerGroup.headers.map(header => (
                    <th
                      key={header.id}
                      className={`px-5 py-3 text-left text-xs font-medium text-muted-foreground uppercase tracking-wider ${
                        (header.column.columnDef.meta as any)?.className ?? ''
                      }`}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  className="group border-b border-border/50 hover:bg-muted/30 transition-colors cursor-pointer"
                  onClick={() => setSelectedLead(row.original)}
                >
                  {row.getVisibleCells().map(cell => (
                    <td
                      key={cell.id}
                      className={`px-5 py-3 ${
                        (cell.column.columnDef.meta as any)?.className ?? ''
                      }`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
              {table.getRowModel().rows.length === 0 && (
                <tr>
                  <td colSpan={columns.length} className="px-5 py-12 text-center">
                    <img src="/assets/generated/states/no-leads.svg" alt="" className="w-24 h-20 mx-auto mb-3 opacity-60" />
                    <p className="text-sm text-muted-foreground">No leads found</p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {table.getPageCount() > 1 && (
          <div className="p-4 border-t border-border flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
                className="h-7 text-xs"
              >
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
                className="h-7 text-xs"
              >
                Next
              </Button>
            </div>
          </div>
        )}
      </motion.div>

      {/* Lead detail slide-over */}
      <Sheet open={!!selectedLead} onOpenChange={(open) => !open && setSelectedLead(null)}>
        <SheetContent className="glass-card-elevated border-l border-border w-[400px] sm:w-[480px]">
          {selectedLead && (
            <>
              <SheetHeader>
                <SheetTitle className="text-foreground">{selectedLead.business_name}</SheetTitle>
                <SheetDescription>{selectedLead.email}</SheetDescription>
              </SheetHeader>

              <ScrollArea className="h-[calc(100vh-140px)] mt-6">
                <div className="space-y-6">
                  {/* Status + Score */}
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className={STATUS_CONFIG[selectedLead.status]?.color ?? ''}>
                      {STATUS_CONFIG[selectedLead.status]?.label ?? selectedLead.status}
                    </Badge>
                    <span className={`text-lg font-bold tabular-nums ${getScoreColor(selectedLead.lead_score)}`}>
                      {selectedLead.lead_score}
                    </span>
                  </div>

                  <Separator />

                  {/* Details */}
                  <div className="space-y-3">
                    <DetailRow label="Industry" value={selectedLead.industry || '—'} />
                    <DetailRow label="Created" value={formatDate(selectedLead.created_at)} />
                    <DetailRow label="Lead ID" value={String(selectedLead.id)} />
                  </div>

                  <Separator />

                  {/* Actions */}
                  <div className="space-y-2">
                    <h4 className="text-xs text-muted-foreground uppercase tracking-wider">Actions</h4>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 border-green/30 text-green hover:bg-green/10"
                        onClick={() => handleAction(selectedLead.id, 'approve')}
                        disabled={actionLoading === `${selectedLead.id}-approve`}
                      >
                        <CheckCircle2 className="w-4 h-4 mr-1" /> Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 border-red/30 text-red hover:bg-red/10"
                        onClick={() => handleAction(selectedLead.id, 'reject')}
                        disabled={actionLoading === `${selectedLead.id}-reject`}
                      >
                        <XCircle className="w-4 h-4 mr-1" /> Reject
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 border-amber/30 text-amber hover:bg-amber/10"
                        onClick={() => handleAction(selectedLead.id, 'escalate')}
                        disabled={actionLoading === `${selectedLead.id}-escalate`}
                      >
                        <AlertTriangle className="w-4 h-4 mr-1" /> Escalate
                      </Button>
                    </div>
                  </div>
                </div>
              </ScrollArea>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm text-foreground">{value}</span>
    </div>
  )
}
