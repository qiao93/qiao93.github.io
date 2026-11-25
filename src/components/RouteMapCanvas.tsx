import {
  useCallback,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
} from 'react';
import type {
  GeoJSONSource as MapboxSource,
  Map as MapboxMap,
} from 'mapbox-gl';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import polyline from '@mapbox/polyline';
import type { Activity } from '../types';
import { hasRoute, routeForActivity } from '../core/routeFallback';
import { MAPBOX_TOKEN } from '../config';
import { useLocale } from '../hooks/useLocale';
import './RouteMap.css';

export interface RouteMapProps {
  activities: Activity[];
  allActivities?: Activity[];
  selectedActivity?: Activity | null;
  dark?: boolean;
  onClearSelection?: () => void;
}

type MapInstance = MapboxMap | maplibregl.Map;
type MapError = { error: Error & { status?: number } };
type StyleEvent = 'idle' | 'load';
type MapLifecycle = {
  on(event: StyleEvent, listener: () => void): unknown;
  on(event: 'error', listener: (event: MapError) => void): unknown;
  off(event: StyleEvent, listener: () => void): unknown;
  off(event: 'error', listener: (event: MapError) => void): unknown;
};
type Basemap = 'mapbox' | 'carto' | 'routes';
type Camera = {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
};

const preferredBasemap = MAPBOX_TOKEN ? 'mapbox' : 'carto';

function routeOnlyStyle(dark: boolean | undefined) {
  return {
    version: 8 as const,
    sources: {},
    layers: [
      {
        id: 'background',
        type: 'background' as const,
        paint: { 'background-color': dark === false ? '#f5f5f5' : '#171717' },
      },
    ],
  };
}

const routeCache = new WeakMap<
  Activity,
  {
    type: 'Feature';
    properties: { type: string };
    geometry: { type: 'LineString'; coordinates: number[][] };
  }[]
>();

export function RouteMapCanvas({
  activities,
  allActivities = activities,
  selectedActivity,
  dark,
  onClearSelection,
}: RouteMapProps) {
  const { locale } = useLocale();
  const zh = locale === 'zh';
  const panelRef = useRef<HTMLElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapInstance | null>(null);
  const styleReadyRef = useRef(false);
  const cameraRef = useRef<Camera | null>(null);
  const fittedRef = useRef<unknown>(null);
  const [provider, setProvider] = useState<Basemap>(preferredBasemap);
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>(
    'loading'
  );
  const [retry, setRetry] = useState(0);

  const displayActivity = useMemo(
    () =>
      selectedActivity
        ? routeForActivity(selectedActivity, allActivities)
        : null,
    [selectedActivity, allActivities]
  );
  const fallbackActivity =
    selectedActivity && !hasRoute(selectedActivity) ? displayActivity : null;

  const routes = useMemo(() => {
    const items = selectedActivity
      ? displayActivity
        ? [displayActivity]
        : []
      : activities;
    return items.flatMap((activity) => {
      const cached = routeCache.get(activity);
      if (cached) return cached;
      if (!activity.summary_polyline) return [];
      try {
        const coordinates = polyline
          .decode(activity.summary_polyline)
          .map(([lat, lng]) => [lng, lat])
          .filter(
            ([lng, lat]) =>
              Number.isFinite(lng) &&
              Number.isFinite(lat) &&
              Math.abs(lng) <= 180 &&
              Math.abs(lat) <= 90
          );
        if (coordinates.length < 2) return [];
        const features = [
          {
            type: 'Feature' as const,
            properties: { type: activity.type },
            geometry: { type: 'LineString' as const, coordinates },
          },
        ];
        routeCache.set(activity, features);
        return features;
      } catch {
        return [];
      }
    });
  }, [activities, selectedActivity, displayActivity]);

  const routeBounds = useMemo(() => {
    const bounds = new maplibregl.LngLatBounds();
    for (const route of routes) {
      for (const coord of route.geometry.coordinates)
        bounds.extend(coord as [number, number]);
    }
    if (bounds.isEmpty()) return null;
    return [
      bounds.getWest(),
      bounds.getSouth(),
      bounds.getEast(),
      bounds.getNorth(),
    ] as [number, number, number, number];
  }, [routes]);

  const fitRoutes = useCallback(() => {
    const map = mapRef.current;
    if (!map || !routeBounds) return;
    map.fitBounds(routeBounds, {
      padding: { top: 35, bottom: 35, left: 35, right: 65 },
      maxZoom: selectedActivity ? 16 : 13,
      duration: window.matchMedia('(prefers-reduced-motion: reduce)').matches
        ? 0
        : 500,
    });
  }, [routeBounds, selectedActivity]);

  const drawRoutes = useCallback(() => {
    const map = mapRef.current;
    if (!map || !styleReadyRef.current) return;
    const data = { type: 'FeatureCollection' as const, features: routes };
    const source =
      map instanceof maplibregl.Map
        ? map.getSource<maplibregl.GeoJSONSource>('routes')
        : map.getSource<MapboxSource>('routes');
    if (source) source.setData(data);
    else {
      map.addSource('routes', { type: 'geojson', data });
      map.addLayer({
        id: 'routes',
        type: 'line',
        source: 'routes',
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': [
            'match',
            ['get', 'type'],
            'Run',
            '#f97316',
            'Ride',
            '#3b82f6',
            '#4dd2ff',
          ],
        },
      });
    }
    map.setPaintProperty('routes', 'line-width', selectedActivity ? 3.5 : 2);
    map.setPaintProperty('routes', 'line-opacity', selectedActivity ? 1 : 0.7);
    if (fittedRef.current !== routes) {
      fittedRef.current = routes;
      fitRoutes();
    }
  }, [routes, selectedActivity, fitRoutes]);

  const drawRoutesAfterStyleLoad = useEffectEvent(drawRoutes);

  useEffect(() => {
    const container = containerRef.current;
    const panel = panelRef.current;
    if (!container || !panel) return;
    let cancelled = false;
    let stopListening: (() => void) | undefined;
    let timer: number | undefined;
    const style =
      provider === 'routes'
        ? routeOnlyStyle(dark)
        : provider === 'mapbox'
          ? `mapbox://styles/mapbox/${dark === false ? 'light' : 'dark'}-v11`
          : `https://basemaps.cartocdn.com/gl/${dark === false ? 'positron' : 'dark-matter'}-gl-style/style.json`;
    const locale: Record<string, string> = zh
      ? {
          'Map.Title': '跑步路线地图',
          'NavigationControl.ZoomIn': '放大',
          'NavigationControl.ZoomOut': '缩小',
          'NavigationControl.ResetBearing': '恢复朝北',
          'FullscreenControl.Enter': '全屏查看',
          'FullscreenControl.Exit': '退出全屏',
          'AttributionControl.ToggleAttribution': '地图来源',
        }
      : {};
    const options = {
      container,
      style,
      center: [121.4, 31.2] as [number, number],
      zoom: 10,
      localIdeographFontFamily: 'sans-serif',
      ...cameraRef.current,
      locale,
    };
    const observer = new ResizeObserver(() => mapRef.current?.resize());
    let settled = false;
    const fallback = () => {
      if (cancelled) return;
      if (!settled) fittedRef.current = null;
      if (provider === 'mapbox') setProvider('carto');
      else if (provider === 'carto') setProvider('routes');
      else setStatus('unavailable');
    };
    const onError = ({ error }: MapError) => {
      const authFailed =
        provider === 'mapbox' &&
        (error.status === 401 ||
          error.status === 403 ||
          error.message.includes('Mapbox access token'));
      if (!settled || authFailed) fallback();
    };
    const onIdle = () => {
      if (!styleReadyRef.current) return;
      settled = true;
      window.clearTimeout(timer);
      setStatus('ready');
    };
    const onStyleLoad = () => {
      styleReadyRef.current = true;
      drawRoutesAfterStyleLoad();
    };

    async function mountMap(container: HTMLDivElement, panel: HTMLElement) {
      // Defer construction until this effect survives React's cleanup.
      await Promise.resolve();
      if (cancelled) return;
      setStatus('loading');
      let instance: MapInstance;
      if (provider === 'mapbox') {
        const [{ default: mapboxgl }] = await Promise.all([
          import('mapbox-gl'),
          import('mapbox-gl/dist/mapbox-gl.css'),
        ]);
        if (cancelled) return;
        const mapbox = new mapboxgl.Map({
          ...options,
          accessToken: MAPBOX_TOKEN,
          language: zh ? 'zh-Hans' : 'en',
        });
        instance = mapbox;
        mapbox.addControl(new mapboxgl.NavigationControl(), 'top-right');
        mapbox.addControl(
          new mapboxgl.FullscreenControl({ container: panel }),
          'top-right'
        );
        mapbox.addControl(
          new mapboxgl.ScaleControl({ unit: 'metric', maxWidth: 90 }),
          'bottom-left'
        );
      } else {
        const libre = new maplibregl.Map(options);
        instance = libre;
        libre.addControl(new maplibregl.NavigationControl(), 'top-right');
        libre.addControl(
          new maplibregl.FullscreenControl({ container: panel }),
          'top-right'
        );
        libre.addControl(
          new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 90 }),
          'bottom-left'
        );
      }
      mapRef.current = instance;
      const events: MapLifecycle = instance;
      events.on('error', onError);
      events.on('idle', onIdle);
      events.on('load', onStyleLoad);
      stopListening = () => {
        events.off('error', onError);
        events.off('idle', onIdle);
        events.off('load', onStyleLoad);
      };
      timer = window.setTimeout(fallback, 10000);
      observer.observe(container);
    }

    void mountMap(container, panel).catch(() => {
      if (cancelled) return;
      if (provider === 'mapbox') setProvider('carto');
      else setStatus('unavailable');
    });
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      stopListening?.();
      observer.disconnect();
      const instance = mapRef.current;
      if (instance) {
        const { lng, lat } = instance.getCenter();
        cameraRef.current = {
          center: [lng, lat],
          zoom: instance.getZoom(),
          bearing: instance.getBearing(),
          pitch: instance.getPitch(),
        };
        instance.remove();
      }
      mapRef.current = null;
      styleReadyRef.current = false;
    };
  }, [provider, dark, zh, retry]);

  useEffect(() => {
    drawRoutes();
  }, [drawRoutes]);

  useEffect(() => {
    let wasFullscreen = document.fullscreenElement === panelRef.current;
    let frame = 0;
    const onFullscreen = () => {
      const isFullscreen = document.fullscreenElement === panelRef.current;
      if (isFullscreen || wasFullscreen) {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          mapRef.current?.resize();
          fitRoutes();
        });
      }
      wasFullscreen = isFullscreen;
    };
    document.addEventListener('fullscreenchange', onFullscreen);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('fullscreenchange', onFullscreen);
    };
  }, [fitRoutes]);

  return (
    <section
      ref={panelRef}
      className="route-map"
      aria-label={zh ? '路线地图' : 'Route map'}
    >
      <div className="route-map-header">
        <div className="min-w-0">
          <h2 className="text-base font-semibold">
            {zh ? '路线地图' : 'Route map'}
          </h2>
          <p
            className="truncate text-xs text-[var(--color-muted)]"
            title={selectedActivity?.name}
          >
            {selectedActivity
              ? `${selectedActivity.name} · ${(selectedActivity.distance / 1000).toFixed(1)} km`
              : `${routes.length.toLocaleString()} ${zh ? '条轨迹' : 'routes'}`}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          {selectedActivity && onClearSelection && (
            <button className="route-map-action" onClick={onClearSelection}>
              {zh ? '返回总览' : 'Overview'}
            </button>
          )}
          <button
            className="route-map-action"
            disabled={!routes.length}
            onClick={fitRoutes}
            title={zh ? '将所有当前轨迹完整放入视野' : 'Fit all current routes'}
          >
            {zh ? '定位轨迹' : 'Fit routes'}
          </button>
        </div>
      </div>
      {fallbackActivity && (
        <p
          role="status"
          className="px-4 pb-2 text-xs text-[var(--color-muted)]"
        >
          {zh
            ? `此活动没有可用的 GPS 轨迹，现显示之前最近一次有轨迹的活动：${fallbackActivity.name}（${fallbackActivity.start_date_local}）。`
            : `This activity has no usable GPS route. Showing the most recent earlier mapped activity: ${fallbackActivity.name} (${fallbackActivity.start_date_local}).`}
        </p>
      )}
      <div className="route-map-body">
        <div ref={containerRef} className="h-full w-full" />
        {!routes.length && (
          <div className="route-map-empty" role="status">
            {zh
              ? selectedActivity
                ? '这次活动没有 GPS 轨迹'
                : '当前筛选没有 GPS 轨迹'
              : 'No GPS route available'}
          </div>
        )}
      </div>
      <div className="route-map-footer">
        <span role="status" aria-live="polite">
          {status === 'unavailable'
            ? zh
              ? '地图无法初始化，请重试'
              : 'Map could not start'
            : status === 'loading'
              ? zh
                ? '正在加载地图…'
                : 'Loading map…'
              : provider === 'routes'
                ? zh
                  ? '底图暂不可用 · 仅显示轨迹'
                  : 'Basemap unavailable · Routes only'
                : provider === 'carto'
                  ? zh
                    ? '备用底图 · CARTO'
                    : 'Alternative basemap · CARTO'
                  : zh
                    ? '底图 · Mapbox'
                    : 'Basemap · Mapbox'}
        </span>
        {(status === 'unavailable' ||
          provider === 'routes' ||
          (provider === 'carto' && !!MAPBOX_TOKEN)) && (
          <button
            className="route-map-action"
            onClick={() => {
              setProvider(preferredBasemap);
              setRetry((value) => value + 1);
            }}
          >
            {provider === 'carto' && MAPBOX_TOKEN
              ? zh
                ? '重试 Mapbox'
                : 'Retry Mapbox'
              : zh
                ? '重试'
                : 'Retry'}
          </button>
        )}
      </div>
    </section>
  );
}
