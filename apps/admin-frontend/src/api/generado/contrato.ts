/**
 * Generado desde contratos/ con openapi-typescript (npm run api:generar). No editar a mano.
 */

export interface paths {
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Login */
        post: operations["login_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logout_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/enrolar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Enrolar Mfa */
        post: operations["enrolar_mfa_auth_mfa_enrolar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/mfa/verificar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Verificar Mfa */
        post: operations["verificar_mfa_auth_mfa_verificar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/refrescar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Refrescar */
        post: operations["refrescar_auth_refrescar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/sesion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Sesion */
        get: operations["sesion_auth_sesion_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Health
         * @description 200 solo si el servicio responde y Postgres contesta; 503 sin detalles internos en cualquier otro caso.
         */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/notificaciones/problemas": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Problemas Notificaciones */
        get: operations["problemas_notificaciones_notificaciones_problemas_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/plataforma/audit-log/verificar-cadena": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Verificar Cadena Plataforma */
        get: operations["verificar_cadena_plataforma_plataforma_audit_log_verificar_cadena_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Leer Settings */
        get: operations["leer_settings_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/settings/{clave}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Editar Setting */
        put: operations["editar_setting_settings__clave__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar */
        get: operations["listar_tenants_get"];
        put?: never;
        /** Alta */
        post: operations["alta_tenants_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ficha */
        get: operations["ficha_tenants__tenant_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/audit-log": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Audit Log */
        get: operations["audit_log_tenants__tenant_id__audit_log_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/audit-log/verificar-cadena": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Verificar Cadena Tenant */
        get: operations["verificar_cadena_tenant_tenants__tenant_id__audit_log_verificar_cadena_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/dar-de-baja": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Dar De Baja */
        post: operations["dar_de_baja_tenants__tenant_id__dar_de_baja_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/facturacion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Leer Facturacion */
        get: operations["leer_facturacion_tenants__tenant_id__facturacion_get"];
        /** Editar Facturacion */
        put: operations["editar_facturacion_tenants__tenant_id__facturacion_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/bookings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Bookings */
        get: operations["bookings_tenants__tenant_id__impersonar_bookings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/bookings/{booking_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Booking */
        get: operations["booking_tenants__tenant_id__impersonar_bookings__booking_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/clientes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Clientes */
        get: operations["clientes_tenants__tenant_id__impersonar_clientes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Packs */
        get: operations["packs_tenants__tenant_id__impersonar_packs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/profesionales": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Profesionales */
        get: operations["profesionales_tenants__tenant_id__impersonar_profesionales_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/impersonar/servicios": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Servicios */
        get: operations["servicios_tenants__tenant_id__impersonar_servicios_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/integraciones/{tipo}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Cargar Integracion */
        put: operations["cargar_integracion_tenants__tenant_id__integraciones__tipo__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/levantar-suspension": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Levantar Suspension */
        post: operations["levantar_suspension_tenants__tenant_id__levantar_suspension_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/limpiezas-calendar/descartar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Descartar Limpieza */
        post: operations["descartar_limpieza_tenants__tenant_id__limpiezas_calendar_descartar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/reactivar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reactivar */
        post: operations["reactivar_tenants__tenant_id__reactivar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/reenviar-invitacion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reenviar */
        post: operations["reenviar_tenants__tenant_id__reenviar_invitacion_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/tenants/{tenant_id}/suspender": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suspender */
        post: operations["suspender_tenants__tenant_id__suspender_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AltaTenantIn */
        AltaTenantIn: {
            /** Name */
            name: string;
            /** Owner Email */
            owner_email: string;
            /** Owner Nombre */
            owner_nombre?: unknown;
            /** Settings */
            settings?: {
                [key: string]: unknown;
            } | null;
            /** Slug */
            slug: string;
        };
        /** BookingDetalleOut */
        BookingDetalleOut: {
            /** Booking Pack Id */
            booking_pack_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Customer Name */
            customer_name: string | null;
            /** Customer Phone */
            customer_phone: string | null;
            /** Deposit Status */
            deposit_status: string;
            /**
             * End At
             * Format: date-time
             */
            end_at: string;
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /** Google Calendar Event Id */
            google_calendar_event_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Pending Expires At */
            pending_expires_at: string | null;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /** Professional Name */
            professional_name: string;
            /** Service Duration Minutes */
            service_duration_minutes: number;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /** Service Name */
            service_name: string;
            /** Service Price */
            service_price: string;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /** Status */
            status: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** BookingResumenOut */
        BookingResumenOut: {
            /** Booking Pack Id */
            booking_pack_id: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Customer Name */
            customer_name: string | null;
            /** Deposit Status */
            deposit_status: string;
            /**
             * End At
             * Format: date-time
             */
            end_at: string;
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Pending Expires At */
            pending_expires_at: string | null;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /** Professional Name */
            professional_name: string;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /** Service Name */
            service_name: string;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /** Status */
            status: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** CadenaAuditOut */
        CadenaAuditOut: {
            /** Fila Invalida Chain Seq */
            fila_invalida_chain_seq: number | null;
            /** Fila Invalida Id */
            fila_invalida_id: string | null;
            /** Integra */
            integra: boolean;
            /** Tenant Id */
            tenant_id: string | null;
        };
        /** ClienteOut */
        ClienteOut: {
            /** Channel */
            channel: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Deleted At */
            deleted_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Name */
            name: string | null;
            /** Phone */
            phone: string | null;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** DescarteLimpiezaIn */
        DescarteLimpiezaIn: {
            /** Event Id */
            event_id?: unknown;
            /** Motivo */
            motivo?: unknown;
        };
        /** DescarteLimpiezaOut */
        DescarteLimpiezaOut: {
            /** Estado */
            estado: string;
            /** Event Id */
            event_id: string;
            /** Tenant Id */
            tenant_id: string;
        };
        /** EnrolarMFAOut */
        EnrolarMFAOut: {
            /** Factor Id */
            factor_id: string;
            /** Qr Code */
            qr_code: string;
            /** Secret */
            secret: string;
            /** Uri */
            uri: string;
        };
        /** EntradaAudit */
        EntradaAudit: {
            /** Action */
            action: string;
            /** Actor Id */
            actor_id: string | null;
            /** Actor Type */
            actor_type: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Entity */
            entity: string | null;
            /** Entity Id */
            entity_id: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Metadata */
            metadata: {
                [key: string]: unknown;
            } | null;
            /** Tenant Id */
            tenant_id: string | null;
        };
        /** Facturacion */
        Facturacion: {
            /** Billing Notes */
            billing_notes: string | null;
            /** Paid Until */
            paid_until: string | null;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** FacturacionIn */
        FacturacionIn: {
            /** Billing Notes */
            billing_notes?: string | null;
            /** Paid Until */
            paid_until?: string | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** IntegracionIn */
        IntegracionIn: {
            /** Config */
            config?: unknown;
            /** Secret */
            secret?: unknown;
        };
        /** InvitacionOut */
        InvitacionOut: {
            /** Estado Invitacion */
            estado_invitacion: string;
            /** Tenant Id */
            tenant_id: string;
        };
        /** LoginIn */
        LoginIn: {
            /** Email */
            email: string;
            /** Password */
            password: string;
            /** Turnstile Token */
            turnstile_token?: string | null;
        };
        /** MFAVerificadaOut */
        MFAVerificadaOut: {
            /** Expira En */
            expira_en: number;
            /**
             * Siguiente Paso
             * @constant
             */
            siguiente_paso: "listo";
        };
        /** NotificacionConProblema */
        NotificacionConProblema: {
            /** Channel */
            channel: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Last Error */
            last_error: string | null;
            /** Retry Count */
            retry_count: number;
            /** Sent At */
            sent_at: string | null;
            /** Status */
            status: string;
            /** Template */
            template: string | null;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** PackOut */
        PackOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Origin */
            origin: string;
            /** Payment Status */
            payment_status: string;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Total Sessions */
            total_sessions: number;
            /** Used Sessions */
            used_sessions: number;
        };
        /** ProblemasNotificaciones */
        ProblemasNotificaciones: {
            /** Limpiezas Calendar */
            limpiezas_calendar: components["schemas"]["EntradaAudit"][];
            /** Notificaciones */
            notificaciones: components["schemas"]["NotificacionConProblema"][];
        };
        /** ProfesionalOut */
        ProfesionalOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Name */
            name: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Tenant User Id */
            tenant_user_id: string | null;
        };
        /** ReenviarInvitacionIn */
        ReenviarInvitacionIn: {
            /** Owner Email */
            owner_email?: string | null;
            /** Owner Nombre */
            owner_nombre?: unknown;
        };
        /** RefrescoOut */
        RefrescoOut: {
            /** Expira En */
            expira_en: number;
        };
        /** ResultadoIntegracion */
        ResultadoIntegracion: {
            /**
             * Integration Id
             * Format: uuid
             */
            integration_id: string;
            /** Integration Type */
            integration_type: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
            /** Webhook Key */
            webhook_key: string;
        };
        /** ServicioOut */
        ServicioOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Deposit Amount */
            deposit_amount: string | null;
            /** Duration Minutes */
            duration_minutes: number;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Name */
            name: string;
            /** Price */
            price: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** SesionOut */
        SesionOut: {
            /** Aal */
            aal: string;
            /** Is Owner */
            is_owner?: boolean | null;
            /** Permisos */
            permisos?: string[] | null;
            /** Tenant Id */
            tenant_id?: string | null;
            /** User Id */
            user_id: string;
        };
        /** SettingEditadoOut */
        SettingEditadoOut: {
            desde: components["schemas"]["ValorJson"];
            hasta: components["schemas"]["ValorJson"];
            /** Key */
            key: string;
        };
        /** SettingIn */
        SettingIn: {
            /**
             * Confirmar Reduccion
             * @default false
             */
            confirmar_reduccion: boolean;
            /** Valor */
            valor: unknown;
        };
        /** SettingsPlataformaOut */
        SettingsPlataformaOut: {
            [key: string]: components["schemas"]["ValorJson"];
        };
        /** SiguientePasoOut */
        SiguientePasoOut: {
            /** Expira En */
            expira_en: number;
            /** Factor Id */
            factor_id?: string | null;
            /**
             * Siguiente Paso
             * @enum {string}
             */
            siguiente_paso: "verificar_mfa" | "enrolar_mfa" | "listo";
        };
        /** TenantAdmin */
        TenantAdmin: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Deactivated At */
            deactivated_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Invitacion Pendiente */
            invitacion_pendiente: boolean;
            /** Name */
            name: string;
            /** Owner Email */
            owner_email: string | null;
            /** Owner Nombre */
            owner_nombre: string | null;
            /** Paid Until */
            paid_until: string | null;
            /** Plan */
            plan: string;
            /** Slug */
            slug: string;
            /** Status */
            status: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        ValorJson: unknown;
        /** VerificarMFAIn */
        VerificarMFAIn: {
            /** Codigo */
            codigo: string;
            /**
             * Factor Id
             * Format: uuid
             */
            factor_id: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    login_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SiguientePasoOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    logout_auth_logout_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
        };
    };
    enrolar_mfa_auth_mfa_enrolar_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EnrolarMFAOut"];
                };
            };
        };
    };
    verificar_mfa_auth_mfa_verificar_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["VerificarMFAIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MFAVerificadaOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    refrescar_auth_refrescar_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RefrescoOut"];
                };
            };
        };
    };
    sesion_auth_sesion_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SesionOut"];
                };
            };
        };
    };
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    problemas_notificaciones_notificaciones_problemas_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProblemasNotificaciones"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verificar_cadena_plataforma_plataforma_audit_log_verificar_cadena_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CadenaAuditOut"];
                };
            };
        };
    };
    leer_settings_settings_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SettingsPlataformaOut"];
                };
            };
        };
    };
    editar_setting_settings__clave__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                clave: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SettingEditadoOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    listar_tenants_get: {
        parameters: {
            query?: {
                status?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantAdmin"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    alta_tenants_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AltaTenantIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitacionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ficha_tenants__tenant_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TenantAdmin"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    audit_log_tenants__tenant_id__audit_log_get: {
        parameters: {
            query?: {
                action?: string | null;
                actor_type?: string | null;
                desde?: string | null;
                hasta?: string | null;
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EntradaAudit"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    verificar_cadena_tenant_tenants__tenant_id__audit_log_verificar_cadena_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CadenaAuditOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    dar_de_baja_tenants__tenant_id__dar_de_baja_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    leer_facturacion_tenants__tenant_id__facturacion_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Facturacion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    editar_facturacion_tenants__tenant_id__facturacion_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FacturacionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Facturacion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    bookings_tenants__tenant_id__impersonar_bookings_get: {
        parameters: {
            query: {
                desde: string;
                hasta: string;
                estados?: string[] | null;
                professional_id?: string | null;
                limite?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookingResumenOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    booking_tenants__tenant_id__impersonar_bookings__booking_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                booking_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BookingDetalleOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    clientes_tenants__tenant_id__impersonar_clientes_get: {
        parameters: {
            query?: {
                busqueda?: string | null;
                limite?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClienteOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    packs_tenants__tenant_id__impersonar_packs_get: {
        parameters: {
            query?: {
                end_customer_id?: string | null;
                limite?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PackOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    profesionales_tenants__tenant_id__impersonar_profesionales_get: {
        parameters: {
            query?: {
                incluir_inactivos?: boolean;
                limite?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfesionalOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    servicios_tenants__tenant_id__impersonar_servicios_get: {
        parameters: {
            query?: {
                incluir_inactivos?: boolean;
                limite?: number;
                offset?: number;
            };
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ServicioOut"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cargar_integracion_tenants__tenant_id__integraciones__tipo__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
                tipo: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["IntegracionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResultadoIntegracion"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    levantar_suspension_tenants__tenant_id__levantar_suspension_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    descartar_limpieza_tenants__tenant_id__limpiezas_calendar_descartar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DescarteLimpiezaIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DescarteLimpiezaOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reactivar_tenants__tenant_id__reactivar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reenviar_tenants__tenant_id__reenviar_invitacion_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: {
            content: {
                "application/json": components["schemas"]["ReenviarInvitacionIn"] | null;
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["InvitacionOut"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    suspender_tenants__tenant_id__suspender_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                tenant_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}
