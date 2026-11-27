using System.Collections.Generic;

public record Product(string Sku, string Name);

public class Catalog
{
    private readonly List<Product> _items = new();
}
