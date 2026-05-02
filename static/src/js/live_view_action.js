/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const { Component } = owl;
const { onWillUnmount, onWillStart, useState } = owl.hooks;

const AXES = ["x", "y", "z", "a"];
const POLL_INTERVAL_MS = 100;

export class MachineControlLiveViewAction extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");

        const params = this.props.action && this.props.action.params ? this.props.action.params : {};
        this.deviceId = params.device_id;

        this.state = useState({
            deviceName: params.device_name || this.props.action.name || "CNC Device",
            status: "unknown",
            lastReadAt: null,
            payload: null,
            error: null,
            isLoading: true,
        });

        this._isDestroyed = false;
        this._pollTimer = null;

        onWillStart(async () => {
            if (!this.deviceId) {
                this.state.error = "Missing device id in action params.";
                this.state.isLoading = false;
                return;
            }
            await this._fetchLiveData();
            this._schedulePoll();
        });

        onWillUnmount(() => {
            this._isDestroyed = true;
            if (this._pollTimer !== null) {
                clearTimeout(this._pollTimer);
                this._pollTimer = null;
            }
        });
    }

    //format the numbers to 4 decimals and display "-" for null/undefined/false values
    _pretty(value) {
        if (value === false || value === null || value === undefined) {
            return "-";
        }
        if (typeof value === "number") {
            return value.toFixed(4);
        }
        return String(value);
    }

    get axisRows() {
        const rows = {
            x: { abs: null, rel: null, mac: null },
            y: { abs: null, rel: null, mac: null },
            z: { abs: null, rel: null, mac: null },
            a: { abs: null, rel: null, mac: null },
        };
        const payload = this.state.payload;
        const axisList = payload && payload.position && payload.position.data ? payload.position.data : [];

        for (const axis of axisList) {
            const idx = Number(axis.axis_index || 0);
            const name = AXES[idx] || "x";
            rows[name] = {
                abs: this._extractValue(axis.absolute),
                rel: this._extractValue(axis.relative),
                mac: this._extractValue(axis.machine),
            };
        }
        return rows;
    }

    _extractValue(positionPart) {
        if (!positionPart) {
            return null;
        }
        if (positionPart.value !== undefined && positionPart.value !== null) {
            return positionPart.value;
        }
        if (positionPart.raw !== undefined && positionPart.raw !== null) {
            return positionPart.raw;
        }
        return null;
    }

    _schedulePoll() {
        this._pollTimer = setTimeout(async () => {
            if (this._isDestroyed) { return; }
            await this._fetchLiveData();
            this._schedulePoll();
        }, POLL_INTERVAL_MS);
    }

    async _fetchLiveData() {
        try {
            const data = await this.orm.silent.call(
                "machine_control.cnc.device",
                "get_live_data",
                [[this.deviceId]]
            );
            this._applyLiveData(data);
        } catch (error) {
            this._setError(error);
        } finally {
            if (this.state.isLoading) { this.state.isLoading = false; }
        }
    }

    _applyLiveData(data) {
        this.state.status = data && data.status ? data.status : "unknown";
        this.state.lastReadAt = data && data.last_read_at ? data.last_read_at : null;
        this.state.payload = data && data.payload ? data.payload : null;
        this.state.error = data && data.error ? data.error : null;
    }

    _setError(error) {
        const message = (error && error.message) || String(error);
        this.state.status = "error";
        this.state.error = message;
        this.notification.add(message, { type: "danger" });
    }

}

MachineControlLiveViewAction.template = "machine_control.LiveViewAction";

registry.category("actions").add("machine_control.live_view", MachineControlLiveViewAction);
