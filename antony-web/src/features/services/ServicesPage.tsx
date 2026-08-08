/**
 * 服务页：五个服务，每个可单独申请。对应小程序 pages/service。
 *
 * 小程序里一屏一张卡纵向排；桌面端改成图文左右交替的横版——五段介绍都很长，
 * 单列铺到 1400px 宽会变成一行七八十个字，读起来很累。
 */
import { Link } from 'react-router-dom'
import { useReveal } from '@/shared/useReveal'
import { services, type Service } from './content'

function ServiceRow({ service, index }: { service: Service; index: number }) {
  const reveal = useReveal<HTMLElement>()
  // 奇数张图片放右边，形成交替节奏
  const flipped = index % 2 === 1

  return (
    <article
      ref={reveal.ref}
      className={`${reveal.className} grid items-center gap-8 lg:grid-cols-2 lg:gap-16`}
    >
      <Link
        to={`/services/${service.id}`}
        className={`group relative block aspect-[4/3] overflow-hidden bg-nero ${
          flipped ? 'lg:order-2' : ''
        }`}
      >
        {/* 图缺失时不出裂图：退回纯色卡头，只显示序号，与小程序 shot-plain 一致 */}
        {service.image ? (
          <img
            src={service.image}
            alt={service.name}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-cover transition-transform duration-[900ms] ease-out group-hover:scale-[1.04]"
          />
        ) : (
          <span className="absolute inset-0 flex items-center justify-center text-[80px] text-su-foto/15">
            {service.ordinal}
          </span>
        )}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-nero/70 to-transparent" />
        <div className="absolute inset-x-0 bottom-0 p-6">
          <span className="text-[12px] tracking-[0.18em] text-su-foto-debole">
            {service.ordinal}
          </span>
          <h3 className="text-on-photo mt-1 text-[22px] tracking-display text-su-foto sm:text-[26px]">
            {service.name}
          </h3>
        </div>
      </Link>

      <div className={flipped ? 'lg:order-1' : ''}>
        <p className="text-[15px] tracking-meta text-nero sm:text-[17px]">{service.tagline}</p>
        <p className="mt-5 text-[14px] leading-loose text-grigio">{service.intro}</p>
        {service.note && (
          <p className="mt-4 text-[13px] text-grigio-chiaro">注 · {service.note}</p>
        )}
        <Link
          to={`/services/${service.id}`}
          className="mt-7 inline-block border border-nero px-10 py-3 text-[13px] tracking-meta transition-colors hover:bg-nero hover:text-bianco"
        >
          立即申请
        </Link>
      </div>
    </article>
  )
}

export function ServicesPage() {
  return (
    <div className="mx-auto max-w-[1440px] px-5 py-16 sm:px-10 sm:py-24">
      <header>
        <h1 className="text-[26px] tracking-display sm:text-[36px]">服务</h1>
        <p className="mt-3 max-w-[38em] text-[14px] leading-loose text-grigio">
          从空间设计到实景落地，五项服务覆盖一个家从图纸到日常的完整周期，每一项都可以单独申请。
        </p>
      </header>

      <div className="mt-16 space-y-20 sm:mt-20 sm:space-y-28">
        {services.map((s, i) => (
          <ServiceRow key={s.id} service={s} index={i} />
        ))}
      </div>
    </div>
  )
}
