import flask
from werkzeug.middleware.proxy_fix import ProxyFix
from warp.config import *

# Prometheus instrumentation
import os
import time
from flask import Response, request, g
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    multiprocess,
)

# application-level metrics (namespaced with 'warp_')
REQUEST_COUNT = Counter(
    'warp_http_requests_total', 'Total HTTP requests (method, endpoint, status)',
    ['method', 'endpoint', 'http_status']
)
REQUEST_LATENCY = Histogram(
    'warp_request_latency_seconds', 'HTTP request latency in seconds', ['endpoint']
)

def metrics_response():
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    data = generate_latest(registry)
    return Response(data, mimetype=CONTENT_TYPE_LATEST)

def create_app():

    app = flask.Flask(__name__)

    # Prepare prometheus multiprocess dir if configured
    prom_dir = os.getenv('PROMETHEUS_MULTIPROC_DIR')
    if prom_dir:
        try:
            os.makedirs(prom_dir, exist_ok=True)
            # By default clear any leftover files from previous runs to avoid stale metrics
            if os.getenv('PROMETHEUS_MULTIPROC_DIR_CLEAR', '1') == '1':
                for fn in os.listdir(prom_dir):
                    path = os.path.join(prom_dir, fn)
                    try:
                        os.remove(path)
                    except Exception:
                        # best-effort cleanup; don't fail app startup
                        pass
        except Exception:
            # ignore errors here; we'll allow app to start even if metrics dir can't be prepared
            pass

    initConfig(app)

    from . import db
    db.init(app)

    from . import ical
    app.register_blueprint(ical.bp)

    from . import view
    app.register_blueprint(view.bp)

    from . import xhr
    app.register_blueprint(xhr.bp, url_prefix='/xhr')

    from . import auth
    from . import auth_mellon
    from . import auth_ldap
    from . import auth_aad
    if 'AUTH_MELLON' in app.config \
       and 'MELLON_ENDPOINT' in app.config \
       and app.config['AUTH_MELLON']:
        app.register_blueprint(auth_mellon.bp)
    elif 'AUTH_LDAP' in app.config \
       and app.config['AUTH_LDAP']:
        app.register_blueprint(auth_ldap.bp)
    elif 'AUTH_AAD' in app.config \
       and app.config['AUTH_AAD']:
        app.register_blueprint(auth_aad.bp)
    else:
        app.register_blueprint(auth.bp)

    @app.route('/health')
    def health():
        # simple health check endpoint
        return 'OK', 200

    # Prometheus metrics endpoint
    @app.route('/metrics')
    def metrics():
        return metrics_response()

    # Instrument requests: measure latency and count
    @app.before_request
    def _prom_start_timer():
        g._prom_start_time = time.time()

    @app.after_request
    def _prom_record_request_data(response):
        try:
            latency = time.time() - g._prom_start_time
        except Exception:
            latency = 0.0
        endpoint = request.endpoint or request.path
        try:
            REQUEST_LATENCY.labels(endpoint=endpoint).observe(latency)
            REQUEST_COUNT.labels(method=request.method, endpoint=endpoint, http_status=response.status_code).inc()
        except Exception:
            # metrics should not break responses
            pass
        return response

    return app
