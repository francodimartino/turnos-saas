import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";
import { obtenerTenant } from "../../api/panel";
import { ConCarga } from "../../compartido/ConCarga";
import { EstadoTenantBadge } from "../../compartido/EstadoTenant";
import { useCarga } from "../../compartido/useCarga";
import { fechaCalendario, fechaHora } from "../../formato";
import { AccionesTenant } from "./AccionesTenant";
import { SeccionFacturacion } from "./SeccionFacturacion";
import { SeccionInvitacion } from "./SeccionInvitacion";
import { SeccionTelegram } from "./SeccionTelegram";

export function FichaTenant() {
  const { id = "" } = useParams();
  const cargar = useCallback(() => obtenerTenant(id), [id]);
  const { carga, recargar } = useCarga(cargar);

  return (
    <section>
      <p>
        <Link to="/tenants">← Tenants</Link>
      </p>
      <ConCarga
        carga={carga}
        hijos={(t) => (
          <>
            <h1>{t.name}</h1>
            <dl className="ficha">
              <dt>Estado</dt>
              <dd>
                <EstadoTenantBadge estado={t.status} />
              </dd>
              <dt>Slug</dt>
              <dd>{t.slug}</dd>
              <dt>Owner</dt>
              <dd>
                {t.owner_nombre ?? "Sin nombre"}
                {t.owner_email && (
                  <>
                    <br />
                    {t.owner_email}
                  </>
                )}
              </dd>
              <dt>Plan</dt>
              <dd>{t.plan}</dd>
              <dt>Pagado hasta</dt>
              <dd>{fechaCalendario(t.paid_until)}</dd>
              <dt>Alta</dt>
              <dd>{fechaHora(t.created_at)}</dd>
              {t.deactivated_at && (
                <>
                  <dt>De baja desde</dt>
                  <dd>{fechaHora(t.deactivated_at)}</dd>
                </>
              )}
            </dl>
            <nav aria-label="Vistas del tenant" className="acciones">
              <Link to={`/tenants/${t.id}/auditoria`}>Auditoría</Link>
              {t.status !== "purged" && <Link to={`/tenants/${t.id}/impersonar`}>Ver como el tenant (solo lectura)</Link>}
            </nav>
            <AccionesTenant tenant={t} alCambiar={recargar} />
            <SeccionInvitacion tenantId={t.id} pendiente={t.invitacion_pendiente} alCambiar={recargar} />
            <SeccionFacturacion tenantId={t.id} />
            {t.status !== "purged" && <SeccionTelegram tenantId={t.id} />}
          </>
        )}
      />
    </section>
  );
}
