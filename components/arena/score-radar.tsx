'use client'

import { useEffect, useRef } from 'react'
import { cn } from '@/lib/cn'

export interface RadarSeries {
  label: string
  color: string
  values: number[]
}

export interface ScoreRadarProps {
  axes: string[]
  series: RadarSeries[]
  className?: string
}

interface ChartLike {
  setOption: (option: unknown) => void
  resize: () => void
  dispose: () => void
}

export function ScoreRadar({ axes, series, className }: ScoreRadarProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let disposed = false
    let chart: ChartLike | null = null

    void (async () => {
      try {
        const [core, charts, components, renderers] = await Promise.all([
          import('echarts/core'),
          import('echarts/charts'),
          import('echarts/components'),
          import('echarts/renderers'),
        ])
        core.use([charts.RadarChart, components.TooltipComponent, components.LegendComponent, renderers.CanvasRenderer])
        if (disposed || !containerRef.current) return
        chart = core.init(containerRef.current) as unknown as ChartLike
        chart.setOption({
          backgroundColor: 'transparent',
          tooltip: {},
          legend: {
            bottom: 0,
            icon: 'circle',
            textStyle: { color: '#9c9ca7', fontSize: 11 },
          },
          radar: {
            indicator: axes.map((name) => ({ name, max: 10 })),
            radius: '68%',
            axisName: { color: '#9c9ca7', fontSize: 11 },
            axisLine: { lineStyle: { color: '#232329' } },
            splitLine: { lineStyle: { color: '#232329' } },
            splitArea: { areaStyle: { color: ['transparent', 'rgba(255,255,255,0.02)'] } },
          },
          series: [
            {
              type: 'radar',
              symbolSize: 3,
              data: series.map((item) => ({
                name: item.label,
                value: item.values,
                lineStyle: { color: item.color, width: 2 },
                itemStyle: { color: item.color },
                areaStyle: { color: item.color, opacity: 0.12 },
              })),
            },
          ],
        })
      } catch {
        // 图形不可用时静默降级（数据仍由下方记分板可读）
      }
    })()

    const onResize = () => chart?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      disposed = true
      window.removeEventListener('resize', onResize)
      chart?.dispose()
    }
  }, [axes, series])

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label={`裁判分雷达图：${series.map((item) => item.label).join('、')}`}
      className={cn('h-60 w-full', className)}
    />
  )
}
