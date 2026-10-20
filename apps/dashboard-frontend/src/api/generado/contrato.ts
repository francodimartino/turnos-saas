/**
 * Generado desde contratos/ con openapi-typescript (npm run api:generar). No editar a mano.
 */

export interface paths {
    "/auth/invitacion/aceptar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Aceptar Invitacion */
        post: operations["aceptar_invitacion_auth_invitacion_aceptar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
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
    "/bloqueos-disponibilidad": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar Bloqueos */
        get: operations["listar_bloqueos_bloqueos_disponibilidad_get"];
        put?: never;
        /** Crear Bloqueo */
        post: operations["crear_bloqueo_bloqueos_disponibilidad_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/bloqueos-disponibilidad/{bloqueo_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ver Bloqueo */
        get: operations["ver_bloqueo_bloqueos_disponibilidad__bloqueo_id__get"];
        put?: never;
        post?: never;
        /** Borrar Bloqueo */
        delete: operations["borrar_bloqueo_bloqueos_disponibilidad__bloqueo_id__delete"];
        options?: never;
        head?: never;
        /** Modificar Bloqueo */
        patch: operations["modificar_bloqueo_bloqueos_disponibilidad__bloqueo_id__patch"];
        trace?: never;
    };
    "/calendar/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Callback */
        get: operations["callback_calendar_callback_get"];
        put?: never;
        /** Callback Json */
        post: operations["callback_json_calendar_callback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/clientes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar */
        get: operations["listar_clientes_get"];
        put?: never;
        /** Alta */
        post: operations["alta_clientes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/clientes/{cliente_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detalle */
        get: operations["detalle_clientes__cliente_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Editar */
        patch: operations["editar_clientes__cliente_id__patch"];
        trace?: never;
    };
    "/clientes/{cliente_id}/anonimizar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Anonimizar */
        post: operations["anonimizar_clientes__cliente_id__anonimizar_post"];
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
    "/local/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Leer Settings */
        get: operations["leer_settings_local_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        /** Modificar Settings */
        patch: operations["modificar_settings_local_settings_patch"];
        trace?: never;
    };
    "/packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar Los Packs */
        get: operations["listar_los_packs_packs_get"];
        put?: never;
        /** Asignar Pack */
        post: operations["asignar_pack_packs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/profesionales": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar Los Profesionales */
        get: operations["listar_los_profesionales_profesionales_get"];
        put?: never;
        /** Crear Profesional */
        post: operations["crear_profesional_profesionales_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/profesionales/{profesional_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ver Profesional */
        get: operations["ver_profesional_profesionales__profesional_id__get"];
        put?: never;
        post?: never;
        /** Borrar Profesional */
        delete: operations["borrar_profesional_profesionales__profesional_id__delete"];
        options?: never;
        head?: never;
        /** Modificar Profesional */
        patch: operations["modificar_profesional_profesionales__profesional_id__patch"];
        trace?: never;
    };
    "/profesionales/{profesional_id}/servicios": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Servicios Del Profesional */
        get: operations["servicios_del_profesional_profesionales__profesional_id__servicios_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/profesionales/{profesional_id}/servicios/{servicio_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Asignar Servicio */
        put: operations["asignar_servicio_profesionales__profesional_id__servicios__servicio_id__put"];
        post?: never;
        /** Quitar Servicio */
        delete: operations["quitar_servicio_profesionales__profesional_id__servicios__servicio_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/profesionales/{professional_id}/calendar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Estado */
        get: operations["estado_profesionales__professional_id__calendar_get"];
        put?: never;
        post?: never;
        /** Desconectar */
        delete: operations["desconectar_profesionales__professional_id__calendar_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/profesionales/{professional_id}/calendar/conexion": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Iniciar */
        post: operations["iniciar_profesionales__professional_id__calendar_conexion_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/reglas-disponibilidad": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar Reglas */
        get: operations["listar_reglas_reglas_disponibilidad_get"];
        put?: never;
        /** Crear Regla */
        post: operations["crear_regla_reglas_disponibilidad_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/reglas-disponibilidad/{regla_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ver Regla */
        get: operations["ver_regla_reglas_disponibilidad__regla_id__get"];
        put?: never;
        post?: never;
        /** Borrar Regla */
        delete: operations["borrar_regla_reglas_disponibilidad__regla_id__delete"];
        options?: never;
        head?: never;
        /** Modificar Regla */
        patch: operations["modificar_regla_reglas_disponibilidad__regla_id__patch"];
        trace?: never;
    };
    "/servicios": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar Los Servicios */
        get: operations["listar_los_servicios_servicios_get"];
        put?: never;
        /** Crear Servicio */
        post: operations["crear_servicio_servicios_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/servicios/{servicio_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Ver Servicio */
        get: operations["ver_servicio_servicios__servicio_id__get"];
        put?: never;
        post?: never;
        /** Borrar Servicio */
        delete: operations["borrar_servicio_servicios__servicio_id__delete"];
        options?: never;
        head?: never;
        /** Modificar Servicio */
        patch: operations["modificar_servicio_servicios__servicio_id__patch"];
        trace?: never;
    };
    "/staff": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar */
        get: operations["listar_staff_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/staff/invitaciones": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Invitar */
        post: operations["invitar_staff_invitaciones_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/staff/{staff_id}/desactivar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Desactivar */
        post: operations["desactivar_staff__staff_id__desactivar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/staff/{staff_id}/owner": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Cambiar Owner */
        put: operations["cambiar_owner_staff__staff_id__owner_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/staff/{staff_id}/permisos": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Cambiar Permisos */
        put: operations["cambiar_permisos_staff__staff_id__permisos_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/staff/{staff_id}/reactivar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Reactivar */
        post: operations["reactivar_staff__staff_id__reactivar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Listar */
        get: operations["listar_turnos_get"];
        put?: never;
        /** Crear */
        post: operations["crear_turnos_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Detalle */
        get: operations["detalle_turnos__turno_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}/aprobar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Aprobar */
        post: operations["aprobar_turnos__turno_id__aprobar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}/cancelar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancelar */
        post: operations["cancelar_turnos__turno_id__cancelar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}/completar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Completar */
        post: operations["completar_turnos__turno_id__completar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}/mover": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Mover */
        post: operations["mover_turnos__turno_id__mover_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/turnos/{turno_id}/rechazar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Rechazar */
        post: operations["rechazar_turnos__turno_id__rechazar_post"];
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
        /** AceptarInvitacionIn */
        AceptarInvitacionIn: {
            /** Password */
            password: string;
            /** Token Hash */
            token_hash: string;
            /** Turnstile Token */
            turnstile_token?: string | null;
        };
        /** AltaCliente */
        AltaCliente: {
            /** Extra Data */
            extra_data?: {
                [key: string]: unknown;
            };
            /** Name */
            name: string;
            /** Phone */
            phone: string;
        };
        /** AltaPack */
        AltaPack: {
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /** Expires At */
            expires_at?: string | null;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /** Total Sessions */
            total_sessions: number;
        };
        /** AltaTurno */
        AltaTurno: {
            /** Booking Pack Id */
            booking_pack_id?: string | null;
            /**
             * End Customer Id
             * Format: uuid
             */
            end_customer_id: string;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
        };
        /** AnonimizacionOut */
        AnonimizacionOut: {
            calendario: components["schemas"]["CalendarioLimpiezaOut"];
            /** Id */
            id: string;
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
        };
        /** AsignacionOut */
        AsignacionOut: {
            /** Confirmation Mode */
            confirmation_mode: string;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
        };
        /** BloqueoIn */
        BloqueoIn: {
            /**
             * End At
             * Format: date-time
             */
            end_at: string;
            /** Professional Id */
            professional_id?: string | null;
            /** Reason */
            reason?: string | null;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
        };
        /** BloqueoOut */
        BloqueoOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * End At
             * Format: date-time
             */
            end_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Professional Id */
            professional_id: string | null;
            /** Reason */
            reason: string | null;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** BloqueoParche */
        BloqueoParche: {
            /** End At */
            end_at?: string | null;
            /** Reason */
            reason?: string | null;
            /** Start At */
            start_at?: string | null;
        };
        /** CalendarioLimpiezaOut */
        CalendarioLimpiezaOut: {
            /** Estado */
            estado: string;
            /** Eventos */
            eventos: number | null;
            /** No Limpiados */
            no_limpiados: number | null;
        };
        /** CalendarioOut */
        CalendarioOut: {
            /** Estado */
            estado: string;
        };
        /** CampoExtraOut */
        CampoExtraOut: {
            /** Key */
            key: string;
            /** Label */
            label?: string | null;
        } & {
            [key: string]: unknown;
        };
        /** ClienteListadoOut */
        ClienteListadoOut: {
            /** Channel */
            channel: string | null;
            /** Created At */
            created_at: string | null;
            /** Deleted At */
            deleted_at: string | null;
            /** Id */
            id: string;
            /** Name */
            name: string | null;
            /** Phone */
            phone: string | null;
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
        };
        /** ClienteOut */
        ClienteOut: {
            /** Channel */
            channel: string | null;
            /** Created At */
            created_at: string | null;
            /** Deleted At */
            deleted_at: string | null;
            /** Extra Data */
            extra_data: {
                [key: string]: unknown;
            } | null;
            /** Id */
            id: string;
            /** Name */
            name: string | null;
            /** Phone */
            phone: string | null;
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
        };
        /** CuerpoCallback */
        CuerpoCallback: {
            /**
             * Code
             * @default
             */
            code: string;
            /**
             * Error
             * @default
             */
            error: string;
            /**
             * State
             * @default
             */
            state: string;
        };
        /** DesconexionOut */
        DesconexionOut: {
            /** Conectado */
            conectado: boolean;
            /** Habia Conexion */
            habia_conexion: boolean;
            /** Profesional Id */
            profesional_id: string;
        };
        /** EdicionCliente */
        EdicionCliente: {
            /** Extra Data */
            extra_data?: {
                [key: string]: unknown;
            } | null;
            /** Name */
            name?: string | null;
            /** Phone */
            phone?: string | null;
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
        /** EstadoCalendarOut */
        EstadoCalendarOut: {
            /** Conectado */
            conectado: boolean;
            /** Conectado Desde */
            conectado_desde: string | null;
            /** Google Configurado */
            google_configurado: boolean;
            /** Profesional Id */
            profesional_id: string;
        };
        /** EstadoStaffOut */
        EstadoStaffOut: {
            /** Id */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Is Owner */
            is_owner: boolean;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** Invitacion */
        Invitacion: {
            /** Email */
            email: string;
            /** Nombre */
            nombre?: unknown;
            /** Permission Keys */
            permission_keys?: string[];
        };
        /** InvitacionOut */
        InvitacionOut: {
            /** Creado */
            creado: boolean;
            /** Tenant User Id */
            tenant_user_id: string;
        };
        /** ListaClientesOut */
        ListaClientesOut: {
            /** Clientes */
            clientes: components["schemas"]["ClienteListadoOut"][];
            /** Limite */
            limite: number;
            /** Offset */
            offset: number;
        };
        /** ListaPacksOut */
        ListaPacksOut: {
            /** Limite */
            limite: number;
            /** Offset */
            offset: number;
            /** Packs */
            packs: components["schemas"]["PackOut"][];
        };
        /** ListaStaffOut */
        ListaStaffOut: {
            /** Staff */
            staff: components["schemas"]["StaffItemOut"][];
        };
        /** ListaTurnosOut */
        ListaTurnosOut: {
            /** Limite */
            limite: number;
            /** Offset */
            offset: number;
            /** Turnos */
            turnos: components["schemas"]["TurnoListadoOut"][];
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
        /** MoverTurno */
        MoverTurno: {
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
        };
        /** OwnerCuerpo */
        OwnerCuerpo: {
            /** Is Owner */
            is_owner: boolean;
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
            /** Total Sessions */
            total_sessions: number;
            /** Used Sessions */
            used_sessions: number;
        };
        /** PermisosCuerpo */
        PermisosCuerpo: {
            /** Permission Keys */
            permission_keys: string[];
        };
        /** PermisosStaffOut */
        PermisosStaffOut: {
            /** Id */
            id: string;
            /** Permission Keys */
            permission_keys: string[];
        };
        /** ProfesionalIn */
        ProfesionalIn: {
            /**
             * Is Active
             * @default true
             */
            is_active: boolean;
            /** Name */
            name: string;
            /** Tenant User Id */
            tenant_user_id?: string | null;
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
        /** ProfesionalParche */
        ProfesionalParche: {
            /** Is Active */
            is_active?: boolean | null;
            /** Name */
            name?: string | null;
            /** Tenant User Id */
            tenant_user_id?: string | null;
        };
        /** RefrescoOut */
        RefrescoOut: {
            /** Expira En */
            expira_en: number;
        };
        /** ReglaIn */
        ReglaIn: {
            /** Day Of Week */
            day_of_week: number;
            /**
             * End Time
             * Format: time
             */
            end_time: string;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /**
             * Start Time
             * Format: time
             */
            start_time: string;
        };
        /** ReglaOut */
        ReglaOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Day Of Week */
            day_of_week: number;
            /**
             * End Time
             * Format: time
             */
            end_time: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Professional Id
             * Format: uuid
             */
            professional_id: string;
            /**
             * Start Time
             * Format: time
             */
            start_time: string;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** ReglaParche */
        ReglaParche: {
            /** Day Of Week */
            day_of_week?: number | null;
            /** End Time */
            end_time?: string | null;
            /** Start Time */
            start_time?: string | null;
        };
        /** ResultadoCallbackOut */
        ResultadoCallbackOut: {
            /** Profesional Id */
            profesional_id: string;
            /** Resultado */
            resultado: string;
        };
        /** ServicioDeProfesionalIn */
        ServicioDeProfesionalIn: {
            /**
             * Confirmation Mode
             * @enum {string}
             */
            confirmation_mode: "auto" | "approval" | "deposit";
        };
        /** ServicioIn */
        ServicioIn: {
            /** Deposit Amount */
            deposit_amount?: number | string | null;
            /** Duration Minutes */
            duration_minutes: number;
            /**
             * Is Active
             * @default true
             */
            is_active: boolean;
            /** Name */
            name: string;
            /** Price */
            price: number | string;
        };
        /** ServicioOut */
        ServicioOut: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Deposit Amount */
            deposit_amount: number | null;
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
            price: number;
            /**
             * Tenant Id
             * Format: uuid
             */
            tenant_id: string;
        };
        /** ServicioParche */
        ServicioParche: {
            /** Deposit Amount */
            deposit_amount?: number | string | null;
            /** Duration Minutes */
            duration_minutes?: number | null;
            /** Is Active */
            is_active?: boolean | null;
            /** Name */
            name?: string | null;
            /** Price */
            price?: number | string | null;
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
        /** SettingsLocalOut */
        SettingsLocalOut: {
            /** Booking Approval Timeout Hours */
            booking_approval_timeout_hours?: number | null;
            /** Booking Max Active Per Customer */
            booking_max_active_per_customer?: number | null;
            /** Booking Max Per Hour */
            booking_max_per_hour?: number | null;
            /** Booking Min Notice Minutes */
            booking_min_notice_minutes?: number | null;
            /** End Customer Extra Fields */
            end_customer_extra_fields?: (string | components["schemas"]["CampoExtraOut"])[] | null;
            /** Local Contacto */
            local_contacto?: string | null;
            /** Local Direccion */
            local_direccion?: string | null;
            /** Local Horarios */
            local_horarios?: string | string[] | null;
        } & {
            [key: string]: unknown;
        };
        /** SettingsOut */
        SettingsOut: {
            settings: components["schemas"]["SettingsLocalOut"];
        };
        /** SettingsParche */
        SettingsParche: {
            /** Settings */
            settings: {
                [key: string]: unknown;
            };
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
        /** StaffItemOut */
        StaffItemOut: {
            /** Created At */
            created_at: string;
            /** Email */
            email: string | null;
            /** Es Yo */
            es_yo: boolean | null;
            /** Id */
            id: string;
            /** Is Active */
            is_active: boolean;
            /** Is Owner */
            is_owner: boolean;
            /** Nombre */
            nombre: string | null;
            /** Permission Keys */
            permission_keys: string[];
        };
        /** TurnoCambiadoOut */
        TurnoCambiadoOut: {
            /** Aviso */
            aviso: string;
            /** Booking Pack Id */
            booking_pack_id: string | null;
            calendario: components["schemas"]["CalendarioOut"];
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
            /**
             * Service Id
             * Format: uuid
             */
            service_id: string;
            /** Service Name */
            service_name: string;
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /** Status */
            status: string;
        };
        /** TurnoDetalleOut */
        TurnoDetalleOut: {
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
            service_price: number;
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /** Status */
            status: string;
        };
        /** TurnoListadoOut */
        TurnoListadoOut: {
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
            /** Sin Cliente Identificable */
            sin_cliente_identificable: boolean;
            /**
             * Start At
             * Format: date-time
             */
            start_at: string;
            /** Status */
            status: string;
        };
        /** UrlConexionOut */
        UrlConexionOut: {
            /** Url */
            url: string;
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
    aceptar_invitacion_auth_invitacion_aceptar_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AceptarInvitacionIn"];
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
    listar_bloqueos_bloqueos_disponibilidad_get: {
        parameters: {
            query?: {
                professional_id?: string | null;
                desde?: string | null;
                hasta?: string | null;
                limite?: number;
                offset?: number;
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
                    "application/json": components["schemas"]["BloqueoOut"][];
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
    crear_bloqueo_bloqueos_disponibilidad_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BloqueoIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BloqueoOut"];
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
    ver_bloqueo_bloqueos_disponibilidad__bloqueo_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                bloqueo_id: string;
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
                    "application/json": components["schemas"]["BloqueoOut"];
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
    borrar_bloqueo_bloqueos_disponibilidad__bloqueo_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                bloqueo_id: string;
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
    modificar_bloqueo_bloqueos_disponibilidad__bloqueo_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                bloqueo_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BloqueoParche"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BloqueoOut"];
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
    callback_calendar_callback_get: {
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
    callback_json_calendar_callback_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CuerpoCallback"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ResultadoCallbackOut"];
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
    listar_clientes_get: {
        parameters: {
            query?: {
                busqueda?: string | null;
                limite?: number;
                offset?: number;
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
                    "application/json": components["schemas"]["ListaClientesOut"];
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
    alta_clientes_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AltaCliente"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClienteOut"];
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
    detalle_clientes__cliente_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                cliente_id: string;
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
                    "application/json": components["schemas"]["ClienteOut"];
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
    editar_clientes__cliente_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                cliente_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EdicionCliente"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClienteOut"];
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
    anonimizar_clientes__cliente_id__anonimizar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                cliente_id: string;
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
                    "application/json": components["schemas"]["AnonimizacionOut"];
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
    leer_settings_local_settings_get: {
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
                    "application/json": components["schemas"]["SettingsOut"];
                };
            };
        };
    };
    modificar_settings_local_settings_patch: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingsParche"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SettingsOut"];
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
    listar_los_packs_packs_get: {
        parameters: {
            query?: {
                end_customer_id?: string | null;
                limite?: number;
                offset?: number;
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
                    "application/json": components["schemas"]["ListaPacksOut"];
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
    asignar_pack_packs_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AltaPack"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PackOut"];
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
    listar_los_profesionales_profesionales_get: {
        parameters: {
            query?: {
                incluir_inactivos?: boolean;
                limite?: number;
                offset?: number;
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
    crear_profesional_profesionales_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfesionalIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfesionalOut"];
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
    ver_profesional_profesionales__profesional_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
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
                    "application/json": components["schemas"]["ProfesionalOut"];
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
    borrar_profesional_profesionales__profesional_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
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
    modificar_profesional_profesionales__profesional_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfesionalParche"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProfesionalOut"];
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
    servicios_del_profesional_profesionales__profesional_id__servicios_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
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
                    "application/json": components["schemas"]["AsignacionOut"][];
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
    asignar_servicio_profesionales__profesional_id__servicios__servicio_id__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
                servicio_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ServicioDeProfesionalIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AsignacionOut"];
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
    quitar_servicio_profesionales__profesional_id__servicios__servicio_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                profesional_id: string;
                servicio_id: string;
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
    estado_profesionales__professional_id__calendar_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                professional_id: string;
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
                    "application/json": components["schemas"]["EstadoCalendarOut"];
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
    desconectar_profesionales__professional_id__calendar_delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                professional_id: string;
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
                    "application/json": components["schemas"]["DesconexionOut"];
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
    iniciar_profesionales__professional_id__calendar_conexion_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                professional_id: string;
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
                    "application/json": components["schemas"]["UrlConexionOut"];
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
    listar_reglas_reglas_disponibilidad_get: {
        parameters: {
            query?: {
                professional_id?: string | null;
                limite?: number;
                offset?: number;
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
                    "application/json": components["schemas"]["ReglaOut"][];
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
    crear_regla_reglas_disponibilidad_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReglaIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReglaOut"];
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
    ver_regla_reglas_disponibilidad__regla_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                regla_id: string;
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
                    "application/json": components["schemas"]["ReglaOut"];
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
    borrar_regla_reglas_disponibilidad__regla_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                regla_id: string;
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
    modificar_regla_reglas_disponibilidad__regla_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                regla_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReglaParche"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReglaOut"];
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
    listar_los_servicios_servicios_get: {
        parameters: {
            query?: {
                incluir_inactivos?: boolean;
                limite?: number;
                offset?: number;
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
    crear_servicio_servicios_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ServicioIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ServicioOut"];
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
    ver_servicio_servicios__servicio_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                servicio_id: string;
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
                    "application/json": components["schemas"]["ServicioOut"];
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
    borrar_servicio_servicios__servicio_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                servicio_id: string;
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
    modificar_servicio_servicios__servicio_id__patch: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                servicio_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ServicioParche"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ServicioOut"];
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
    listar_staff_get: {
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
                    "application/json": components["schemas"]["ListaStaffOut"];
                };
            };
        };
    };
    invitar_staff_invitaciones_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["Invitacion"];
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
    desactivar_staff__staff_id__desactivar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                staff_id: string;
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
                    "application/json": components["schemas"]["EstadoStaffOut"];
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
    cambiar_owner_staff__staff_id__owner_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                staff_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["OwnerCuerpo"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EstadoStaffOut"];
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
    cambiar_permisos_staff__staff_id__permisos_put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                staff_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PermisosCuerpo"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PermisosStaffOut"];
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
    reactivar_staff__staff_id__reactivar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                staff_id: string;
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
                    "application/json": components["schemas"]["EstadoStaffOut"];
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
    listar_turnos_get: {
        parameters: {
            query: {
                desde: string;
                hasta: string;
                estado?: string[] | null;
                professional_id?: string | null;
                limite?: number;
                offset?: number;
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
                    "application/json": components["schemas"]["ListaTurnosOut"];
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
    crear_turnos_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AltaTurno"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
    detalle_turnos__turno_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
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
                    "application/json": components["schemas"]["TurnoDetalleOut"];
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
    aprobar_turnos__turno_id__aprobar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
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
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
    cancelar_turnos__turno_id__cancelar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
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
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
    completar_turnos__turno_id__completar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
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
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
    mover_turnos__turno_id__mover_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["MoverTurno"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
    rechazar_turnos__turno_id__rechazar_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                turno_id: string;
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
                    "application/json": components["schemas"]["TurnoCambiadoOut"];
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
}
