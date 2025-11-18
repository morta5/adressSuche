/**
 * Address Autocomplete Library
 *
 * A comprehensive JavaScript library for address autocomplete with the following features:
 * - Single field or multi-field (street+housenumber, postcode, city) input support
 * - Keyboard (arrow keys), mouse, and touch support
 * - Automatic house number addition assumption
 * - Optional address validation after completion
 * - Hook for callback after complete address entry
 * - User-friendly autocomplete suggestions
 *
 * @version 1.1.0
 * @author Farshid Hakimy
 */

class AddressAutocomplete {
  /**
   * Initialize the address autocomplete
   * @param {Object} options - Configuration options
   * @param {string|HTMLElement} options.input - Single input field selector or element
   * @param {Object} options.inputs - Multi-field inputs { street, housenumber, postcode, city }
   * @param {string} options.apiUrl - Base URL of the address API
   * @param {boolean} options.validateOnComplete - Enable validation after complete entry (default: true)
   * @param {Function} options.onComplete - Callback function called after address is selected
   * @param {Function} options.onValidate - Callback function called after validation
   * @param {number} options.debounceMs - Debounce delay in milliseconds (default: 300)
   * @param {number} options.minChars - Minimum characters before suggestions (default: 2)
   * @param {number} options.maxSuggestions - Maximum number of suggestions (default: 10)
   * @param {Object} options.proximity - Proximity search { lat, lon }
   * @param {string} options.defaultCity - Optional default city filter sent to the API
   * @param {Function} options.onVerificationSuccess - Callback with validated coordinates { street, housenumber, postcode, city, latitude, longitude, raw }
   * @param {string} options.theme - Theme for styling: 'light' or 'dark' (default: 'light')
   * @param {boolean} options.appendHousenumber - Always append house number field (default: true)
   */
  constructor(options = {}) {
    // Validate options
    if (!options.input && !options.inputs) {
      throw new Error('Either "input" or "inputs" must be provided');
    }
    if (!options.apiUrl) {
      throw new Error("API URL is required");
    }

    // Configuration
    this.apiUrl = options.apiUrl.replace(/\/$/, ""); // Remove trailing slash
    this.validateOnComplete = options.validateOnComplete !== false;
    this.onComplete = options.onComplete || null;
    this.onValidate = options.onValidate || null;
    this.onVerificationSuccess = options.onVerificationSuccess || null;
    this.debounceMs = options.debounceMs || 300;
    this.minChars = options.minChars || 2;
    this.maxSuggestions = options.maxSuggestions || 10;
    this.proximity = options.proximity || null;
    this.defaultCity = options.defaultCity || null;
    this.theme = options.theme || "light";
    this.appendHousenumber = options.appendHousenumber !== false;

    // Touch interaction state
    this._touchState = null;

    // Verification state for single input mode
    this.verificationMode = false;
    this.pendingVerificationAddress = null;
    this.verificationTimer = null;
    this.lastVerificationValue = "";
    this.validationAbortController = null;
    this._boundTouchStart = (event) => this._onSuggestionTouchStart(event);
    this._boundTouchMove = (event) => this._onSuggestionTouchMove(event);
    this._boundTouchEnd = (event) => this._onSuggestionTouchEnd(event);
    this._boundTouchCancel = () => this._onSuggestionTouchCancel();

    // Mode: 'single' or 'multi'
    this.mode = options.input ? "single" : "multi";

    // Input elements
    if (this.mode === "single") {
      this.input =
        typeof options.input === "string"
          ? document.querySelector(options.input)
          : options.input;
      if (!this.input) {
        throw new Error("Input element not found");
      }
    } else {
      this.inputs = {
        street: this._getElement(options.inputs.street),
        housenumber: this._getElement(options.inputs.housenumber),
        postcode: this._getElement(options.inputs.postcode),
        city: this._getElement(options.inputs.city),
      };
    }

    // State
    this.suggestions = [];
    this.selectedIndex = -1;
    this.isOpen = false;
    this.debounceTimer = null;
    this.currentRequest = null;
    this.lastSelectedAddress = null;

    // Create UI elements
    this._createSuggestionBox();
    this._attachEventListeners();
    this._injectStyles();
  }

  /**
   * Determine active city filter for requests
   * @private
   */
  _getActiveCityFilter() {
    if (this.mode === "multi" && this.inputs.city?.value) {
      const value = this.inputs.city.value.trim();
      if (value) return value;
    }

    if (this.defaultCity) {
      return this.defaultCity;
    }

    return null;
  }

  /**
   * Convert API suggestion to internal representation
   * @param {Object} suggestion - Raw API suggestion
   * @returns {Object}
   * @private
   */
  _mapApiSuggestion(suggestion) {
    if (!suggestion || typeof suggestion !== "object") {
      return {
        street: "",
        postcode: "",
        city: "",
      };
    }

    return {
      id: suggestion.street_id ?? null,
      street: suggestion.name || "",
      postcode: suggestion.postal_code || "",
      city: suggestion.city || "",
      latitude: suggestion.latitude ?? null,
      longitude: suggestion.longitude ?? null,
      distanceKm: suggestion.distance_km ?? null,
      raw: suggestion,
    };
  }

  /**
   * Get element from selector or element
   * @private
   */
  _getElement(input) {
    if (!input) return null;
    return typeof input === "string" ? document.querySelector(input) : input;
  }

  /**
   * Create the suggestion dropdown box
   * @private
   */
  _createSuggestionBox() {
    this.suggestionBox = document.createElement("div");
    this.suggestionBox.className = `address-autocomplete-suggestions address-autocomplete-theme-${this.theme}`;
    this.suggestionBox.style.display = "none";
    document.body.appendChild(this.suggestionBox);

    // Create loading indicator
    this.loadingIndicator = document.createElement("div");
    this.loadingIndicator.className = "address-autocomplete-loading";
    this.loadingIndicator.textContent = "Lade Vorschläge...";
  }

  /**
   * Attach event listeners
   * @private
   */
  _attachEventListeners() {
    if (this.mode === "single") {
      this._attachInputListeners(this.input);
    } else {
      // Attach to all input fields in multi mode
      Object.values(this.inputs).forEach((input) => {
        if (input) this._attachInputListeners(input);
      });
    }

    // Global click listener to close suggestions
    document.addEventListener("click", (e) => {
      if (
        !this.suggestionBox.contains(e.target) &&
        !this._isInputField(e.target)
      ) {
        this.close();
      }
    });

    // Suggestion box event listeners
    this.suggestionBox.addEventListener("mousedown", (e) => {
      e.preventDefault(); // Prevent input blur
    });

    this.suggestionBox.addEventListener("click", (e) => {
      const item = e.target.closest(".address-autocomplete-item");
      if (item) {
        const index = parseInt(item.dataset.index);
        this.selectSuggestion(index);
      }
    });

    // Touch support with scroll-friendly behaviour
    this.suggestionBox.addEventListener("touchstart", this._boundTouchStart, {
      passive: true,
    });
    this.suggestionBox.addEventListener("touchmove", this._boundTouchMove, {
      passive: true,
    });
    this.suggestionBox.addEventListener("touchend", this._boundTouchEnd);
    this.suggestionBox.addEventListener("touchcancel", this._boundTouchCancel);
  }

  /**
   * Attach listeners to a specific input
   * @private
   */
  _attachInputListeners(input) {
    input.addEventListener("input", (e) => {
      this._handleInput(e);
    });
    input.addEventListener("keydown", (e) => {
      this._handleKeydown(e);
    });

    // For multi-field without housenumber, attach verification check to street input
    if (
      this.mode === "multi" &&
      !this.inputs.housenumber &&
      input === this.inputs.street
    ) {
      input.addEventListener("input", () => {
        if (this.verificationMode) {
          this._scheduleVerificationCheck();
        }
      });
    }

    input.addEventListener("focus", (e) => {
      if (this.suggestions.length > 0) {
        this.open();
      }
    });

    input.addEventListener("blur", (e) => {
      // Delay to allow click on suggestion
      setTimeout(() => {
        if (!this.suggestionBox.matches(":hover")) {
          this.close();
        }
      }, 200);
    });
  }

  /**
   * Check if element is one of our input fields
   * @private
   */
  _isInputField(element) {
    if (this.mode === "single") {
      return element === this.input;
    } else {
      return Object.values(this.inputs).includes(element);
    }
  }

  /**
   * Handle touch interactions on suggestion list without blocking scroll
   * @private
   */
  _onSuggestionTouchStart(event) {
    if (event.touches.length !== 1) return;
    const touch = event.touches[0];
    const item = event.target.closest(".address-autocomplete-item");
    this._touchState = {
      startX: touch.clientX,
      startY: touch.clientY,
      moved: false,
      item,
    };
  }

  _onSuggestionTouchMove(event) {
    if (!this._touchState || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const dx = Math.abs(touch.clientX - this._touchState.startX);
    const dy = Math.abs(touch.clientY - this._touchState.startY);
    if (dx > 10 || dy > 10) {
      this._touchState.moved = true;
    }
  }

  _onSuggestionTouchEnd(event) {
    if (!this._touchState) return;
    const { item, moved } = this._touchState;
    const targetItem =
      event.target.closest(".address-autocomplete-item") || item;
    if (!moved && targetItem) {
      event.preventDefault();
      const index = parseInt(targetItem.dataset.index);
      if (!Number.isNaN(index)) {
        this.selectSuggestion(index);
      }
    }
    this._touchState = null;
  }

  _onSuggestionTouchCancel() {
    this._touchState = null;
  }

  /**
   * Handle input changes
   * @private
   */
  _handleInput(event) {
    clearTimeout(this.debounceTimer);

    if (this.mode === "single" && this.verificationMode) {
      const currentValue = (this.input?.value || "").trim();
      const baseStreet = this.pendingVerificationAddress?.street || "";

      if (!currentValue) {
        this._exitVerificationMode();
      } else if (
        baseStreet &&
        currentValue.toLowerCase().startsWith(baseStreet.toLowerCase())
      ) {
        this._scheduleVerificationCheck();
        return;
      } else {
        this._exitVerificationMode();
      }
    }

    // In multi-field mode, only fetch suggestions for street input
    if (this.mode === "multi" && event.target !== this.inputs.street) {
      return;
    }

    const query = this._getCurrentQuery();

    if (query.length < this.minChars) {
      this._clearVerificationTimer();
      this.close();
      return;
    }

    this.debounceTimer = setTimeout(() => {
      this._fetchSuggestions(query);
    }, this.debounceMs);
  }

  /**
   * Get current query from input(s)
   * @private
   */
  _getCurrentQuery() {
    if (this.mode === "single") {
      return this.input.value.trim();
    } else {
      // Combine all filled fields for search
      const parts = [];
      if (this.inputs.street?.value) {
        if (!this.inputs.housenumber) {
          // In multi-field without housenumber, parse street to exclude housenumber from query
          const parsed = this._parseInput(this.inputs.street.value);
          parts.push(parsed.street);
        } else {
          parts.push(this.inputs.street.value);
        }
      }
      if (this.inputs.housenumber?.value)
        parts.push(this.inputs.housenumber.value);
      if (this.inputs.postcode?.value) parts.push(this.inputs.postcode.value);
      if (this.inputs.city?.value) parts.push(this.inputs.city.value);
      return parts.join(" ").trim();
    }
  }

  /**
   * Handle keyboard navigation
   * @private
   */
  _handleKeydown(event) {
    if (this.mode === "single" && this.verificationMode) {
      if (event.key === "Enter") {
        event.preventDefault();
        this._performVerificationCheck();
        return;
      }
      if (event.key === "Escape") {
        this._exitVerificationMode();
      }
    }

    if (!this.isOpen) return;

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        this.selectedIndex = Math.min(
          this.selectedIndex + 1,
          this.suggestions.length - 1,
        );
        this._updateSelection();
        break;

      case "ArrowUp":
        event.preventDefault();
        this.selectedIndex = Math.max(this.selectedIndex - 1, -1);
        this._updateSelection();
        break;

      case "Enter":
        event.preventDefault();
        if (this.selectedIndex >= 0) {
          this.selectSuggestion(this.selectedIndex);
        }
        break;

      case "Escape":
        event.preventDefault();
        this.close();
        break;
    }
  }

  /**
   * Fetch suggestions from API
   * @private
   */
  async _fetchSuggestions(query) {
    // Cancel previous request
    if (this.currentRequest) {
      this.currentRequest.abort();
    }

    const controller = new AbortController();
    this.currentRequest = controller;

    try {
      // Show loading
      this._showLoading();

      // Build query parameters
      const params = new URLSearchParams({
        query: query,
        limit: String(this.maxSuggestions),
      });

      const activeCity = this._getActiveCityFilter();
      if (activeCity) {
        params.append("city", activeCity);
      }

      if (this.proximity) {
        if (this.proximity.lat) params.append("latitude", this.proximity.lat);
        if (this.proximity.lon) params.append("longitude", this.proximity.lon);
      }

      const response = await fetch(`${this.apiUrl}/autocomplete?${params}`, {
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const data = await response.json();
      this.suggestions = Array.isArray(data)
        ? data.map((item) => this._mapApiSuggestion(item))
        : [];
      this._renderSuggestions();
      this.open();
    } catch (error) {
      if (error.name !== "AbortError") {
        console.error("Error fetching suggestions:", error);
        this.suggestions = [];
        this._showError("Fehler beim Laden der Vorschläge");
      }
    } finally {
      this.currentRequest = null;
    }
  }

  /**
   * Show loading indicator
   * @private
   */
  _showLoading() {
    this.suggestionBox.innerHTML = "";
    this.suggestionBox.appendChild(this.loadingIndicator);
    this.suggestionBox.style.display = "block";
    this._positionSuggestionBox();
  }

  /**
   * Show error message
   * @private
   */
  _showError(message) {
    this.suggestionBox.innerHTML = `<div class="address-autocomplete-error">${message}</div>`;
    this.suggestionBox.style.display = "block";
    this._positionSuggestionBox();
  }

  /**
   * Render suggestions in the dropdown
   * @private
   */
  _renderSuggestions() {
    this.suggestionBox.innerHTML = "";
    this.selectedIndex = -1;

    if (this.suggestions.length === 0) {
      this.suggestionBox.innerHTML =
        '<div class="address-autocomplete-empty">Keine Adressen gefunden</div>';
      this._renderAttribution();
      return;
    }

    this.suggestions.forEach((suggestion, index) => {
      const item = document.createElement("div");
      item.className = "address-autocomplete-item";
      item.dataset.index = index;

      // Format address display
      const street = suggestion.street || "";
      const postcode = suggestion.postcode || "";
      const city = suggestion.city || "";

      // Primary line: street (emphasize missing house number)
      const primaryLine = document.createElement("div");
      primaryLine.className = "address-autocomplete-primary";
      primaryLine.innerHTML = `<strong>${this._escapeHtml(street)}</strong>`;

      if (this.appendHousenumber) {
        primaryLine.innerHTML +=
          ' <span class="address-autocomplete-hint">(Hausnummer eingeben)</span>';
      }

      // Secondary line: postcode and city
      const secondaryLine = document.createElement("div");
      secondaryLine.className = "address-autocomplete-secondary";
      secondaryLine.textContent = [postcode, city].filter(Boolean).join(" ");

      item.appendChild(primaryLine);
      item.appendChild(secondaryLine);

      if (
        typeof suggestion.distanceKm === "number" &&
        Number.isFinite(suggestion.distanceKm)
      ) {
        const metaLine = document.createElement("div");
        metaLine.className = "address-autocomplete-meta";
        metaLine.textContent = `${suggestion.distanceKm.toFixed(1)} km entfernt`;
        item.appendChild(metaLine);
      }
      this.suggestionBox.appendChild(item);
    });
    this._renderAttribution();
  }

  /**
   * Render required data source attributions in the suggestion box
   * @private
   */
  _renderAttribution() {
    // Remove any existing attribution
    const old = this.suggestionBox.querySelector('.address-autocomplete-attribution');
    if (old) old.remove();
    const attribution = document.createElement('div');
    attribution.className = 'address-autocomplete-attribution';
    attribution.innerHTML =
      '<small>Adressen: <a href="https://arcg.is/0nXTyK" target="_blank" rel="noopener">Esri Deutschland (CC BY 4.0)</a>, ' +
      '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap (ODbL)</a></small>';
    attribution.style.cssText = 'padding: 8px 12px 4px 12px; color: #888; text-align: left; background: none;';
    this.suggestionBox.appendChild(attribution);
  }

  /**
   * Update visual selection
   * @private
   */
  _updateSelection() {
    const items = this.suggestionBox.querySelectorAll(
      ".address-autocomplete-item",
    );
    items.forEach((item, index) => {
      if (index === this.selectedIndex) {
        item.classList.add("selected");
        item.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        item.classList.remove("selected");
      }
    });
  }

  /**
   * Select a suggestion by index
   * @param {number} index - Index of the suggestion to select
   */
  selectSuggestion(index) {
    if (index < 0 || index >= this.suggestions.length) return;

    const suggestion = this.suggestions[index];
    this._exitVerificationMode();
    this.lastSelectedAddress = {
      street: suggestion.street || "",
      postcode: suggestion.postcode || "",
      city: suggestion.city || "",
      latitude: suggestion.latitude ?? null,
      longitude: suggestion.longitude ?? null,
      distanceKm: suggestion.distanceKm ?? null,
      raw: suggestion.raw || null,
    };

    if (this.mode === "single") {
      // In single mode, format as: "Street [CURSOR], Postcode City"
      const street = suggestion.street || "";
      const postcode = suggestion.postcode || "";
      const city = suggestion.city || "";

      // Insert space after street for house number entry
      let addressText = street + " ";
      const cursorPosition = addressText.length; // Position after street and space

      if (postcode && city) {
        addressText += `, ${postcode} ${city}`;
      }

      this.input.value = addressText;

      // Set cursor position after street name
      this.input.focus();
      this.input.setSelectionRange(cursorPosition, cursorPosition);

      this._enterVerificationMode(suggestion);
    } else {
      // Fill multi-field inputs
      if (this.inputs.street)
        this.inputs.street.value = suggestion.street || "";
      if (this.inputs.postcode)
        this.inputs.postcode.value = suggestion.postcode || "";
      if (this.inputs.city) this.inputs.city.value = suggestion.city || "";

      // If no house number field exists and appendHousenumber is true, append space to street and keep input open
      if (!this.inputs.housenumber && this.appendHousenumber) {
        if (this.inputs.street) {
          this.inputs.street.value = (suggestion.street || "") + " ";
          this.inputs.street.focus();
        }
        this._enterVerificationMode(suggestion);
      } else if (this.inputs.housenumber) {
        this.inputs.housenumber.focus();
      }
    }

    this.close();

    // If house number is provided in suggestion, trigger completion
    if (suggestion.housenumber || !this.appendHousenumber) {
      this._handleAddressComplete(suggestion);
    }
  }

  /**
   * Handle complete address entry
   * @private
   */
  async _handleAddressComplete(address) {
    let finalAddress = { ...address };

    // If in multi-mode, get house number from input
    if (this.mode === "multi" && this.inputs.housenumber?.value) {
      finalAddress.housenumber = this.inputs.housenumber.value;
    }

    // Validate if enabled
    if (this.validateOnComplete) {
      try {
        const validatedAddress = await this.validateAddress(finalAddress);
        finalAddress = validatedAddress;

        if (this.onValidate) {
          this.onValidate(validatedAddress);
        }
      } catch (error) {
        console.error("Validation error:", error);
      }
    }

    // Call completion callback
    if (this.onComplete) {
      this.onComplete(finalAddress);
    }
  }

  /**
   * Validate an address with the API
   * @param {Object} address - Address object to validate
   * @returns {Promise<Object>} Validated address
   */
  async validateAddress(address) {
    let controller;
    try {
      if (!address.street) {
        throw new Error("Street name is required for validation");
      }

      const params = new URLSearchParams({
        street_name: address.street,
        house_number: address.housenumber || "",
      });

      if (address.city) {
        params.append("city", address.city);
      }

      if (this.proximity) {
        if (this.proximity.lat) params.append("latitude", this.proximity.lat);
        if (this.proximity.lon) params.append("longitude", this.proximity.lon);
      }

      if (this.validationAbortController) {
        this.validationAbortController.abort();
      }

      controller = new AbortController();
      this.validationAbortController = controller;

      const response = await fetch(`${this.apiUrl}/validate?${params}`, {
        method: "GET",
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Validation API error: ${response.status}`);
      }

      const data = await response.json();

      return {
        valid: Boolean(data.exists),
        exists: Boolean(data.exists),
        street: data.street_name || address.street || "",
        housenumber: data.house_number || address.housenumber || "",
        postcode: data.postal_code || address.postcode || "",
        city: data.city || address.city || "",
        latitude: data.latitude ?? null,
        longitude: data.longitude ?? null,
        distanceKm: data.distance_km ?? null,
        raw: data,
      };
    } catch (error) {
      if (error.name !== "AbortError") {
        console.error("Address validation failed:", error);
      }
      throw error;
    } finally {
      if (controller && this.validationAbortController === controller) {
        this.validationAbortController = null;
      }
    }
  }

  /**
   * Manually trigger address completion with current values
   */
  async complete() {
    const address = {};

    if (this.mode === "single") {
      Object.assign(address, this._parseSingleInput());
    } else {
      // Get from multi-field inputs
      address.street = this.inputs.street?.value?.trim() || "";
      address.housenumber = this.inputs.housenumber?.value?.trim() || "";
      address.postcode = this.inputs.postcode?.value?.trim() || "";
      address.city = this.inputs.city?.value?.trim() || "";
    }

    if (address.street) {
      await this._handleAddressComplete(address);
    }
  }

  /**
   * Parse single input field into address components
   * @returns {Object}
   * @private
   */
  _enterVerificationMode(baseSuggestion) {
    if (
      this.mode !== "single" &&
      !(this.mode === "multi" && !this.inputs.housenumber)
    )
      return;
    if (!this.validateOnComplete) return;
    if (baseSuggestion?.housenumber || this.appendHousenumber === false) {
      return;
    }

    this.verificationMode = true;
    this.pendingVerificationAddress = {
      street: baseSuggestion?.street || "",
      postcode: baseSuggestion?.postcode || "",
      city: baseSuggestion?.city || "",
    };
    this.lastVerificationValue = "";
    this.suggestions = [];
    this.close();
    this._clearVerificationTimer();
  }

  _exitVerificationMode() {
    this._clearVerificationTimer();
    this.verificationMode = false;
    this.pendingVerificationAddress = null;
    this.lastVerificationValue = "";
  }

  _clearVerificationTimer() {
    if (this.verificationTimer) {
      clearTimeout(this.verificationTimer);
      this.verificationTimer = null;
    }
  }

  _scheduleVerificationCheck() {
    this._clearVerificationTimer();
    this.verificationTimer = setTimeout(() => {
      this._performVerificationCheck();
    }, this.debounceMs);
  }

  async _performVerificationCheck() {
    this._clearVerificationTimer();

    if (
      !this.verificationMode ||
      (this.mode !== "single" &&
        !(this.mode === "multi" && !this.inputs.housenumber))
    ) {
      return;
    }

    const inputToParse =
      this.mode === "single" ? this.input : this.inputs.street;
    const parsed = this._parseInput(inputToParse?.value || "");
    const street =
      parsed.street || this.pendingVerificationAddress?.street || "";
    const housenumber = parsed.housenumber || "";
    const postcode =
      parsed.postcode || this.pendingVerificationAddress?.postcode || "";
    const city = parsed.city || this.pendingVerificationAddress?.city || "";

    if (!street || !housenumber) {
      return;
    }

    const signature = `${street}|${housenumber}|${city}|${postcode}`;
    if (signature === this.lastVerificationValue) {
      return;
    }

    this.lastVerificationValue = signature;

    const addressToValidate = {
      street,
      housenumber,
      postcode,
      city,
    };

    try {
      const result = await this.validateAddress(addressToValidate);

      if (this.onValidate) {
        this.onValidate(result);
      }

      if (result.valid || result.exists) {
        if (this.pendingVerificationAddress) {
          this.pendingVerificationAddress.street = result.street || street;
          this.pendingVerificationAddress.postcode =
            result.postcode || postcode;
          this.pendingVerificationAddress.city = result.city || city;
        }

        if (this.onComplete) {
          this.onComplete(result);
        }

        if (this.onVerificationSuccess) {
          this.onVerificationSuccess({
            street: result.street || street,
            housenumber: result.housenumber || housenumber,
            postcode: result.postcode || postcode,
            city: result.city || city,
            latitude: result.latitude ?? null,
            longitude: result.longitude ?? null,
            raw: result.raw ?? result,
          });
        }
      }
    } catch (error) {
      if (error.name !== "AbortError") {
        console.error("Verification error:", error);
      }
    }
  }

  _parseInput(value) {
    const trimmedValue = value.trim();
    if (!trimmedValue) return {};

    let streetPart = trimmedValue;
    let cityPart = "";

    const commaIndex = trimmedValue.indexOf(",");
    if (commaIndex !== -1) {
      streetPart = trimmedValue.slice(0, commaIndex).trim();
      cityPart = trimmedValue.slice(commaIndex + 1).trim();
    }

    let street = streetPart;
    let housenumber = "";

    const streetMatch = streetPart.match(/^(.*?)(\s+(\d+[a-zA-Z\/-]*))$/);
    if (streetMatch) {
      street = streetMatch[1].trim();
      housenumber = streetMatch[3].trim();
    }

    let postcode = "";
    let city = cityPart;

    const cityMatch = cityPart.match(/^(\d{4,5})\s*(.*)$/);
    if (cityMatch) {
      postcode = cityMatch[1].trim();
      city = cityMatch[2].trim();
    }

    return {
      street,
      housenumber,
      postcode,
      city,
    };
  }

  _parseSingleInput() {
    return this._parseInput(this.input?.value || "");
  }

  /**
   * Open the suggestion dropdown
   */
  open() {
    if (this.suggestions.length === 0) return;

    this.suggestionBox.style.display = "block";
    this._positionSuggestionBox();
    this.isOpen = true;
  }

  /**
   * Close the suggestion dropdown
   */
  close() {
    this.suggestionBox.style.display = "none";
    this.isOpen = false;
    this.selectedIndex = -1;
  }

  /**
   * Position the suggestion box relative to input
   * @private
   */
  _positionSuggestionBox() {
    let referenceElement;

    if (this.mode === "single") {
      referenceElement = this.input;
    } else {
      // Position relative to the first visible input
      referenceElement = Object.values(this.inputs).find(
        (input) => input && input.offsetParent !== null,
      );
    }

    if (!referenceElement) return;

    const rect = referenceElement.getBoundingClientRect();
    const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
    const scrollLeft =
      window.pageXOffset || document.documentElement.scrollLeft;

    this.suggestionBox.style.position = "absolute";
    this.suggestionBox.style.top = `${rect.bottom + scrollTop}px`;
    this.suggestionBox.style.left = `${rect.left + scrollLeft}px`;
    this.suggestionBox.style.width = `${Math.max(rect.width, 300)}px`;
  }

  /**
   * Inject CSS styles
   * @private
   */
  _injectStyles() {
    if (document.getElementById("address-autocomplete-styles")) return;

    const style = document.createElement("style");
    style.id = "address-autocomplete-styles";
    style.textContent = `
            .address-autocomplete-suggestions {
                position: absolute;
                z-index: 10000;
                max-height: 400px;
                overflow-y: auto;
                border: 1px solid #ddd;
                border-radius: 4px;
                background: white;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                font-size: 14px;
            }

            .address-autocomplete-theme-dark {
                background: #2d2d2d;
                border-color: #444;
                color: #e0e0e0;
            }

            .address-autocomplete-item {
                padding: 12px 16px;
                cursor: pointer;
                border-bottom: 1px solid #f0f0f0;
                position: relative;
                transition: background-color 0.15s ease;
            }

            .address-autocomplete-theme-dark .address-autocomplete-item {
                border-bottom-color: #444;
            }

            .address-autocomplete-item:hover,
            .address-autocomplete-item.selected {
                background-color: #f5f5f5;
            }

            .address-autocomplete-theme-dark .address-autocomplete-item:hover,
            .address-autocomplete-theme-dark .address-autocomplete-item.selected {
                background-color: #3d3d3d;
            }

            .address-autocomplete-item:last-child {
                border-bottom: none;
            }

            .address-autocomplete-primary {
                font-size: 15px;
                margin-bottom: 4px;
                color: #333;
            }

            .address-autocomplete-theme-dark .address-autocomplete-primary {
                color: #e0e0e0;
            }

            .address-autocomplete-secondary {
                font-size: 13px;
                color: #666;
            }

            .address-autocomplete-theme-dark .address-autocomplete-secondary {
                color: #999;
            }

            .address-autocomplete-hint {
                color: #999;
                font-size: 12px;
                font-weight: normal;
                font-style: italic;
            }

            .address-autocomplete-theme-dark .address-autocomplete-hint {
                color: #777;
            }

            .address-autocomplete-meta {
                font-size: 12px;
                color: #888;
                margin-top: 6px;
            }

            .address-autocomplete-theme-dark .address-autocomplete-meta {
                color: #aaa;
            }

            .address-autocomplete-score {
                position: absolute;
                bottom: 0;
                left: 0;
                height: 2px;
                background: linear-gradient(90deg, #4CAF50 0%, #8BC34A 100%);
                transition: width 0.3s ease;
            }

            .address-autocomplete-loading,
            .address-autocomplete-empty,
            .address-autocomplete-error {
                padding: 16px;
                text-align: center;
                color: #999;
            }

            .address-autocomplete-error {
                color: #f44336;
            }

            .address-autocomplete-theme-dark .address-autocomplete-loading,
            .address-autocomplete-theme-dark .address-autocomplete-empty {
                color: #777;
            }

            .address-autocomplete-attribution {
                padding: 8px 12px;
                font-size: 11px;
                color: #888;
                background: #fafafa;
                border-top: 1px solid #e0e0e0;
                text-align: center;
            }

            .address-autocomplete-attribution a {
                color: #666;
                text-decoration: none;
            }

            .address-autocomplete-attribution a:hover {
                text-decoration: underline;
            }

            .address-autocomplete-theme-dark .address-autocomplete-attribution {
                background: #2a2a2a;
                border-top-color: #444;
                color: #999;
            }

            .address-autocomplete-theme-dark .address-autocomplete-attribution a {
                color: #aaa;
            }

            /* Scrollbar styling */
            .address-autocomplete-suggestions::-webkit-scrollbar {
                width: 8px;
            }

            .address-autocomplete-suggestions::-webkit-scrollbar-track {
                background: #f1f1f1;
            }

            .address-autocomplete-theme-dark .address-autocomplete-suggestions::-webkit-scrollbar-track {
                background: #2d2d2d;
            }

            .address-autocomplete-suggestions::-webkit-scrollbar-thumb {
                background: #c1c1c1;
                border-radius: 4px;
            }

            .address-autocomplete-theme-dark .address-autocomplete-suggestions::-webkit-scrollbar-thumb {
                background: #555;
            }

            .address-autocomplete-suggestions::-webkit-scrollbar-thumb:hover {
                background: #a8a8a8;
            }

            /* Responsive */
            @media (max-width: 600px) {
                .address-autocomplete-suggestions {
                    max-height: 300px;
                }

                .address-autocomplete-item {
                    padding: 10px 12px;
                }
            }

            /* Touch-friendly tap targets */
            @media (pointer: coarse) {
                .address-autocomplete-item {
                    padding: 14px 16px;
                    min-height: 48px;
                }
            }
        `;

    document.head.appendChild(style);
  }

  /**
   * Escape HTML to prevent XSS
   * @private
   */
  _escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Destroy the autocomplete instance
   */
  destroy() {
    // Remove event listeners
    if (this.suggestionBox && this.suggestionBox.parentNode) {
      this.suggestionBox.removeEventListener(
        "touchstart",
        this._boundTouchStart,
      );
      this.suggestionBox.removeEventListener("touchmove", this._boundTouchMove);
      this.suggestionBox.removeEventListener("touchend", this._boundTouchEnd);
      this.suggestionBox.removeEventListener(
        "touchcancel",
        this._boundTouchCancel,
      );
      this.suggestionBox.parentNode.removeChild(this.suggestionBox);
    }

    // Clear timers
    clearTimeout(this.debounceTimer);
    this._clearVerificationTimer();

    // Cancel pending requests
    if (this.currentRequest) {
      this.currentRequest.abort();
    }
    if (this.validationAbortController) {
      this.validationAbortController.abort();
      this.validationAbortController = null;
    }
  }

  /**
   * Update configuration
   * @param {Object} options - New configuration options
   */
  updateConfig(options) {
    if (options.apiUrl !== undefined)
      this.apiUrl = options.apiUrl.replace(/\/$/, "");
    if (options.validateOnComplete !== undefined)
      this.validateOnComplete = options.validateOnComplete;
    if (options.onComplete !== undefined) this.onComplete = options.onComplete;
    if (options.onValidate !== undefined) this.onValidate = options.onValidate;
    if (options.debounceMs !== undefined) this.debounceMs = options.debounceMs;
    if (options.minChars !== undefined) this.minChars = options.minChars;
    if (options.maxSuggestions !== undefined)
      this.maxSuggestions = options.maxSuggestions;
    if (options.proximity !== undefined) this.proximity = options.proximity;
    if (options.defaultCity !== undefined)
      this.defaultCity = options.defaultCity;
    if (options.onVerificationSuccess !== undefined)
      this.onVerificationSuccess = options.onVerificationSuccess;
    if (options.theme !== undefined) {
      this.theme = options.theme;
      this.suggestionBox.className = `address-autocomplete-suggestions address-autocomplete-theme-${this.theme}`;
    }
  }
}

// Export for different module systems
if (typeof module !== "undefined" && module.exports) {
  module.exports = AddressAutocomplete;
}

if (typeof define === "function" && define.amd) {
  define([], function () {
    return AddressAutocomplete;
  });
}

if (typeof window !== "undefined") {
  window.AddressAutocomplete = AddressAutocomplete;
}
