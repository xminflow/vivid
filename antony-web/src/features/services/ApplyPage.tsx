/**
 * 服务申请表。对应小程序 pages/apply。
 *
 * 表单完全由 content.ts 的 fields / uploads 驱动，加字段不用动这个文件。
 * 图片选完立刻传 COS，表单里只存对象键；提交时把键按组交给服务端
 * （app/models.py 的 ServiceApplicationIn，字段名是 fields 和 images）。
 */
import { useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, submitServiceApplication } from '@/shared/api'
import {
  FieldRow,
  NumberInput,
  SelectInput,
  TextInput,
  TextareaInput,
} from '@/shared/components/Field'
import { ImageUploader } from '@/shared/components/ImageUploader'
import { SubmitBar, SubmitDone } from '@/shared/components/SubmitBar'
import { MAX_PER_GROUP, findService, yearOptions, type ServiceField } from './content'

export function ApplyPage() {
  const { serviceId } = useParams()
  const navigate = useNavigate()
  const service = useMemo(() => findService(serviceId), [serviceId])

  const [values, setValues] = useState<Record<string, string>>({})
  const [images, setImages] = useState<Record<string, string[]>>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  const years = useMemo(yearOptions, [])

  // 服务 id 是路由参数，用户可以随便改。找不到就给个明确出口，不要白屏
  if (!service) {
    return (
      <div className="mx-auto max-w-[760px] px-5 py-24 text-center sm:px-10">
        <h1 className="text-[22px] tracking-display">没有这项服务</h1>
        <Link
          to="/services"
          className="mt-8 inline-block border border-nero px-10 py-3 text-[13px] tracking-meta transition-colors hover:bg-nero hover:text-bianco"
        >
          看看全部服务
        </Link>
      </div>
    )
  }

  const set = (id: string) => (v: string) => setValues((s) => ({ ...s, [id]: v }))

  function renderField(f: ServiceField) {
    const value = values[f.id] ?? ''

    switch (f.type) {
      case 'select':
        return (
          <SelectInput
            value={value}
            onChange={set(f.id)}
            // options 为空的是购买年份，按当前年生成，写死会年年过期
            options={f.options?.length ? f.options : years}
          />
        )
      case 'number':
        return <NumberInput value={value} onChange={set(f.id)} unit={f.unit} />
      case 'textarea':
        return (
          <TextareaInput
            value={value}
            onChange={set(f.id)}
            placeholder={f.placeholder}
            maxLength={f.maxlength}
            rows={4}
          />
        )
      case 'phone':
        return (
          <TextInput
            value={value}
            onChange={set(f.id)}
            placeholder={f.placeholder}
            inputMode="tel"
            maxLength={11}
            autoComplete="tel"
          />
        )
      case 'region':
        // 小程序用的是原生省市区选择器，web 上没有等价控件。做级联要额外带一份
        // 行政区划数据（几十 KB）和一个新依赖，而服务端存的本来就是一个字符串，
        // 所以这里退回文本输入，用 placeholder 约定格式
        return (
          <TextInput
            value={value}
            onChange={set(f.id)}
            placeholder="省 / 市 / 区"
            maxLength={60}
          />
        )
      default:
        return (
          <TextInput
            value={value}
            onChange={set(f.id)}
            placeholder={f.placeholder}
            maxLength={f.maxlength}
            autoComplete={f.id === 'name' ? 'name' : undefined}
          />
        )
    }
  }

  function validate(): string {
    for (const f of service!.fields) {
      const v = (values[f.id] ?? '').trim()
      if (f.required && !v) return `请填写${f.label}`
      if (f.type === 'phone' && v && !/^1[3-9]\d{9}$/.test(v)) return '联系方式格式不正确'
    }
    return ''
  }

  async function handleSubmit() {
    const invalid = validate()
    if (invalid) {
      setError(invalid)
      return
    }

    setSubmitting(true)
    setError('')
    try {
      // name 和 phone 服务端是独立的列（要用来联系客户），其余字段整体进 fields
      const rest: Record<string, string> = {}
      for (const f of service!.fields) {
        if (f.id === 'name' || f.id === 'phone') continue
        const v = (values[f.id] ?? '').trim()
        if (v) rest[f.id] = v
      }

      await submitServiceApplication({
        serviceId: service!.id,
        name: (values.name ?? '').trim(),
        phone: (values.phone ?? '').trim(),
        fields: rest,
        images,
      })
      setDone(true)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '提交失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <div className="mx-auto max-w-[860px] px-5 sm:px-10">
        <SubmitDone
          title="已收到您的申请"
          body={`${service.name}\n我们会尽快电话与您联系`}
          actionLabel="返回服务列表"
          onAction={() => navigate('/services')}
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[860px] px-5 py-16 sm:px-10 sm:py-24">
      <header>
        <span className="text-[12px] tracking-[0.18em] text-grigio-chiaro">{service.ordinal}</span>
        <h1 className="mt-2 text-[26px] tracking-display sm:text-[34px]">{service.name}</h1>
        <p className="mt-3 text-[15px] tracking-meta text-grigio">{service.tagline}</p>
        <p className="mt-6 text-[14px] leading-loose text-grigio">{service.intro}</p>
        {service.note && <p className="mt-4 text-[13px] text-grigio-chiaro">注 · {service.note}</p>}
      </header>

      <div className="mt-14 grid gap-x-8 gap-y-7 sm:grid-cols-2">
        {service.fields.map((f) => (
          <FieldRow
            key={f.id}
            label={f.label}
            required={f.required}
            wide={f.type === 'textarea'}
            hint={
              f.type === 'textarea' && f.maxlength
                ? `${(values[f.id] ?? '').length}/${f.maxlength}`
                : undefined
            }
          >
            {renderField(f)}
          </FieldRow>
        ))}
      </div>

      {service.uploads.length > 0 && (
        <div className="mt-12 space-y-9">
          {service.uploads.map((g) => (
            <ImageUploader
              key={g.id}
              label={g.label}
              // scene 只用来在 COS 里分目录，服务端会清洗掉非法字符
              scene={`${service.id}-${g.id}`}
              max={MAX_PER_GROUP}
              onChange={(keys) => setImages((s) => ({ ...s, [g.id]: keys }))}
            />
          ))}
        </div>
      )}

      <SubmitBar
        submitting={submitting}
        label="提交申请"
        error={error}
        onSubmit={() => void handleSubmit()}
      />
    </div>
  )
}
