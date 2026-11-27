package shop;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {
    @PostMapping("/orders")
    public ResponseEntity<String> create() {
        return ResponseEntity.status(201).body("created");
    }
}
