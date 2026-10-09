import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, RefreshCw, Pencil, Camera } from "lucide-react";
import {
  api,
  apiErrorMessage,
  type Page,
  type PricePolicyRead,
  type PricePolicyCreate,
  type PricePolicyUpdate,
  type BillingUnit,
  type RouteBindingRead,
} from "../../lib/client";
import { usePolling } from "../../lib/usePolling";
import { isValidDecimalString, formatMoney } from "../../lib/decimal";
import Pagination from "../../components/ui/Pagination";
import StatusBadge from "../../components/ui/StatusBadge";
import ErrorBanner from "../../components/ui/ErrorBanner";
import EmptyState from "../../components/ui/EmptyState";
import Modal from "../../components/ui/Modal";
import { Field, Select, TextInput, NumberInput, Toggle } from "../../components/ui/Form";

const PAGE_SIZE = 20;

interface PricePolicyForm {
  id: string | null;
  routeBindingId: string;
  billingUnit: BillingUnit;
  currency: string;
  unitScale: string;
  requestPrice: string;
  inputPrice: string;
  outputPrice: string;
  enabled: boolean;
  name: string;
}

const emptyForm: PricePolicyForm = {
  id: null,
  routeBindingId: "",
  billingUnit: "request",
  currency: "USD",
  unitScale: "1",
  requestPrice: "",
  inputPrice: "",
  outputPrice: "",
  enabled: true,
  name: "",
};

function routeLabel(binding: RouteBindingRead): string {
  if (binding.upstream_model) return `${binding.upstream_model} (${binding.model_alias_id})`;
  return binding.model_alias_id;
}

export default function PricingPage() {
  const [page, setPage] = useState(0);
  const [routeFilter, setRouteFilter] = useState("");
  const [enabledFilter, setEnabledFilter] = useState<"" | "true" | "false">("");
  const [unitFilter, setUnitFilter] = useState<BillingUnit | "">("");

  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<PricePolicyForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const list = usePolling<Page<PricePolicyRead>>(
    () =>
      api.listPricePolicies({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        ...(routeFilter ? { route_binding_id: routeFilter } : {}),
        ...(enabledFilter ? { enabled: enabledFilter === "true" } : {}),
        ...(unitFilter ? { billing_unit: unitFilter } : {}),
      }),
    60_000,
    [page, routeFilter, enabledFilter, unitFilter],
  );
  const routeBindings = usePolling<RouteBindingRead[] | null>(
    async () => (await api.listRouteBindings({ limit: 200 })).items,
    60_000,
    [],
  );

  const openCreate = () => {
    setForm({ ...emptyForm, routeBindingId: routeFilter });
    setActionError(null);
    setModalOpen(true);
  };

  const openEdit = (p: PricePolicyRead) => {
    setForm({
      id: p.id,
      routeBindingId: p.route_binding_id,
      billingUnit: p.billing_unit,
      currency: p.currency,
      unitScale: String(p.unit_scale),
      requestPrice: p.request_price ?? "",
      inputPrice: p.input_price ?? "",
      outputPrice: p.output_price ?? "",
      enabled: p.enabled,
      name: p.name ?? "",
    });
    setActionError(null);
    setModalOpen(true);
  };

  const formValid = () => {
    if (!form.currency.trim()) return false;
    if (!/^[0-9]+$/.test(form.unitScale.trim())) return false;
    if (parseInt(form.unitScale, 10) < 1) return false;
    if (form.id === null && !form.routeBindingId) return false;
    if (form.billingUnit === "token") {
      if (!isValidDecimalString(form.inputPrice) || !isValidDecimalString(form.outputPrice)) return false;
    } else {
      if (!isValidDecimalString(form.requestPrice)) return false;
    }
    return true;
  };

  const onCreate = async () => {
    const unitScale = parseInt(form.unitScale, 10);
    const body: PricePolicyCreate = {
      route_binding_id: form.routeBindingId,
      billing_unit: form.billingUnit,
      currency: form.currency.trim(),
      unit_scale: unitScale,
      enabled: form.enabled,
    };
    if (form.billingUnit === "token") {
      body.input_price = form.inputPrice.trim();
      body.output_price = form.outputPrice.trim();
    } else {
      body.request_price = form.requestPrice.trim();
    }
    if (form.name.trim()) body.name = form.name.trim();

    setBusy(true);
    setActionError(null);
    try {
      await api.createPricePolicy(body);
      setModalOpen(false);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onUpdate = async () => {
    if (!form.id) return;
    const original = (list.data?.items ?? []).find((p) => p.id === form.id);
    if (!original) return;
    const patch: PricePolicyUpdate = {};

    const currency = form.currency.trim();
    if (currency !== original.currency) patch.currency = currency;
    const unitScale = parseInt(form.unitScale, 10);
    if (Number.isFinite(unitScale) && unitScale !== original.unit_scale) patch.unit_scale = unitScale;
    if (form.billingUnit !== original.billing_unit) patch.billing_unit = form.billingUnit;
    if (form.enabled !== original.enabled) patch.enabled = form.enabled;
    const name = form.name.trim();
    if (name !== (original.name ?? "")) patch.name = name === "" ? null : name;

    if (form.billingUnit === "token") {
      const inputPrice = form.inputPrice.trim();
      const outputPrice = form.outputPrice.trim();
      if (inputPrice !== (original.input_price ?? "")) patch.input_price = inputPrice;
      if (outputPrice !== (original.output_price ?? "")) patch.output_price = outputPrice;
      if (original.request_price != null) patch.request_price = null;
    } else {
      const requestPrice = form.requestPrice.trim();
      if (requestPrice !== (original.request_price ?? "")) patch.request_price = requestPrice;
      if (original.input_price != null) patch.input_price = null;
      if (original.output_price != null) patch.output_price = null;
    }

    if (Object.keys(patch).length === 0) {
      setModalOpen(false);
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await api.updatePricePolicy(form.id, patch);
      setModalOpen(false);
      list.refresh();
    } catch (err) {
      setActionError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const total = list.data?.total ?? 0;

  return (
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold">Pricing</h2>
          <p className="text-sm text-[var(--ag-text-muted)] mt-1">
            Route price policies. Editing a policy does not change already-captured{" "}
            <Link to="/accounting/snapshots" className="text-[var(--ag-accent)] hover:underline">
              snapshots
            </Link>
            .
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white transition-colors"
          >
            <Plus size={16} /> New policy
          </button>
          <button
            type="button"
            onClick={list.refresh}
            className="inline-flex items-center gap-2 text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] hover:bg-[var(--ag-surface-2)] transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="max-w-xs">
          <Field label="Route binding" htmlFor="pricing-route-filter">
            <Select
              id="pricing-route-filter"
              value={routeFilter}
              onChange={(e) => {
                setRouteFilter(e.target.value);
                setPage(0);
              }}
            >
              <option value="">All route bindings</option>
              {(routeBindings.data ?? []).map((b) => (
                <option key={b.id} value={b.id}>{routeLabel(b)}</option>
              ))}
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Billing unit" htmlFor="pricing-unit-filter">
            <Select
              id="pricing-unit-filter"
              value={unitFilter}
              onChange={(e) => {
                setUnitFilter(e.target.value as BillingUnit | "");
                setPage(0);
              }}
            >
              <option value="">All units</option>
              <option value="request">Request</option>
              <option value="token">Token</option>
              <option value="image">Image</option>
              <option value="minute">Minute</option>
            </Select>
          </Field>
        </div>
        <div className="max-w-xs">
          <Field label="Enabled" htmlFor="pricing-enabled-filter">
            <Select
              id="pricing-enabled-filter"
              value={enabledFilter}
              onChange={(e) => {
                setEnabledFilter(e.target.value as "" | "true" | "false");
                setPage(0);
              }}
            >
              <option value="">All</option>
              <option value="true">Enabled</option>
              <option value="false">Disabled</option>
            </Select>
          </Field>
        </div>
      </div>

      <ErrorBanner message={actionError} />
      {list.error ? <p className="text-sm text-[var(--ag-danger)]">Error: {list.error}</p> : null}

      <div className="bg-[var(--ag-surface)] border border-[var(--ag-border)] rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--ag-border)] text-left text-xs text-[var(--ag-text-muted)] uppercase tracking-wider">
              <th className="px-4 py-3">Route</th>
              <th className="px-4 py-3">Billing</th>
              <th className="px-4 py-3">Price</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody>
            {list.loading && !list.data ? (
              <tr><td colSpan={5} className="px-4 py-6 text-[var(--ag-text-muted)]">Loading…</td></tr>
            ) : (list.data?.items ?? []).length === 0 ? (
              <tr><td colSpan={5}><EmptyState message="No price policies." /></td></tr>
            ) : (
              (list.data?.items ?? []).map((p) => (
                <tr key={p.id} className="border-b border-[var(--ag-border)] last:border-0 hover:bg-[var(--ag-surface-2)]/40">
                  <td className="px-4 py-3 font-mono text-xs text-[var(--ag-text-muted)]">{p.route_binding_id}</td>
                  <td className="px-4 py-3 text-xs">{p.billing_unit}</td>
                  <td className="px-4 py-3 text-xs">
                    {p.billing_unit === "token"
                      ? `${formatMoney(p.input_price, p.currency)} in / ${formatMoney(p.output_price, p.currency)} out (per ${p.unit_scale})`
                      : formatMoney(p.request_price, p.currency)}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge label={p.enabled ? "Enabled" : "Disabled"} tone={p.enabled ? "active" : "inactive"} />
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    <div className="inline-flex items-center gap-2">
                      <Link
                        to={`/accounting/snapshots?route=${p.route_binding_id}`}
                        className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors"
                        title="Snapshots for this route"
                      >
                        <Camera size={13} /> Snapshots
                      </Link>
                      <button type="button" onClick={() => openEdit(p)} className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-md border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                        <Pencil size={13} /> Edit
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />

      {modalOpen ? (
        <Modal title={form.id === null ? "New price policy" : "Edit price policy"} onClose={() => setModalOpen(false)}>
          <div className="space-y-4">
            <Field label="Route binding" htmlFor="pricing-route">
              <Select
                id="pricing-route"
                value={form.routeBindingId}
                onChange={(e) => setForm({ ...form, routeBindingId: e.target.value })}
                disabled={form.id !== null}
              >
                <option value="">Select a route binding…</option>
                {(routeBindings.data ?? []).map((b) => (
                  <option key={b.id} value={b.id}>{routeLabel(b)}</option>
                ))}
              </Select>
            </Field>
            <Field label="Billing unit" htmlFor="pricing-unit">
              <Select
                id="pricing-unit"
                value={form.billingUnit}
                onChange={(e) => setForm({ ...form, billingUnit: e.target.value as BillingUnit })}
              >
                <option value="request">Request</option>
                <option value="token">Token</option>
                <option value="image">Image</option>
                <option value="minute">Minute</option>
              </Select>
            </Field>
            <Field label="Currency" htmlFor="pricing-currency">
              <TextInput id="pricing-currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} placeholder="USD" />
            </Field>
            {form.billingUnit === "token" ? (
              <>
                <Field label="Unit scale" htmlFor="pricing-unit-scale" hint="Prices are per this many units (e.g. 1,000,000 tokens).">
                  <NumberInput id="pricing-unit-scale" min={1} step={1} value={form.unitScale} onChange={(e) => setForm({ ...form, unitScale: e.target.value })} />
                </Field>
                <Field label="Input price" htmlFor="pricing-input">
                  <TextInput id="pricing-input" value={form.inputPrice} onChange={(e) => setForm({ ...form, inputPrice: e.target.value })} placeholder="0.000001" inputMode="decimal" />
                </Field>
                <Field label="Output price" htmlFor="pricing-output">
                  <TextInput id="pricing-output" value={form.outputPrice} onChange={(e) => setForm({ ...form, outputPrice: e.target.value })} placeholder="0.000002" inputMode="decimal" />
                </Field>
              </>
            ) : (
              <Field label="Request price" htmlFor="pricing-request">
                <TextInput id="pricing-request" value={form.requestPrice} onChange={(e) => setForm({ ...form, requestPrice: e.target.value })} placeholder="0.0001" inputMode="decimal" />
              </Field>
            )}
            <Field label="Name (optional)" htmlFor="pricing-name">
              <TextInput id="pricing-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Optional" />
            </Field>
            <Toggle id="pricing-enabled" label="Enabled" checked={form.enabled} onChange={(v) => setForm({ ...form, enabled: v })} />
            {form.id !== null ? (
              <p className="text-xs text-[var(--ag-text-muted)]">Only one enabled policy may exist per route binding.</p>
            ) : null}
            <ErrorBanner message={actionError} />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => setModalOpen(false)} className="text-sm px-3 py-2 rounded-lg border border-[var(--ag-border)] text-[var(--ag-text-muted)] hover:text-[var(--ag-text)] transition-colors">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void (form.id === null ? onCreate() : onUpdate())}
                disabled={busy || !formValid()}
                className="text-sm px-3 py-2 rounded-lg bg-[var(--ag-accent)] hover:bg-[var(--ag-accent-hover)] text-white disabled:opacity-50 transition-colors"
              >
                {busy ? "Saving…" : form.id === null ? "Create" : "Save changes"}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
