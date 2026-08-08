import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'

/**
 * 带「全部」选项的下拉筛选。
 *
 * Radix 的 Select 不允许 value 为空串（空串是「未选中」的内部表示，传进去会报错），
 * 所以这里用一个哨兵值代表「不筛」，对外仍然是空串——查询参数为空时后端就不加这个条件。
 *
 * 触发器里没用 SelectValue：选中「全部」时它会把选项文字显示出来，
 * 而未筛选状态应该显示的是这一栏叫什么（「跟进状态」），不是「全部」。
 */
const ALL = '__all__'

interface Option {
  value: string
  label: string
}

interface Props {
  value: string
  options: readonly Option[]
  placeholder: string
  className?: string
  onChange: (value: string) => void
}

export function SelectFilter({ value, options, placeholder, className, onChange }: Props) {
  const selected = options.find((option) => option.value === value)

  return (
    <Select value={value || ALL} onValueChange={(next) => onChange(next === ALL ? '' : next)}>
      <SelectTrigger className={className} aria-label={placeholder}>
        {selected ? (
          <span className="truncate">{selected.label}</span>
        ) : (
          <span className="truncate text-muted-foreground">{placeholder}</span>
        )}
      </SelectTrigger>
      {/* popper：菜单贴着触发器下方弹出。默认的 item-aligned 会把菜单摞到选中项上，
          筛选栏这种一行好几个下拉的场景，弹出位置会飘到视口边缘 */}
      <SelectContent position="popper" align="start">
        <SelectItem value={ALL}>全部</SelectItem>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
