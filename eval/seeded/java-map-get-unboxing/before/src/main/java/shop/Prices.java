package shop;

import java.util.Map;

public class Prices {
    private final Map<String, Integer> cents;

    public Prices(Map<String, Integer> cents) {
        this.cents = cents;
    }
}
