package cache

var items = map[string]string{}

func Get(k string) string {
	return items[k]
}
